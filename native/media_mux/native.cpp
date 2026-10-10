// MIT licensed project code. FFmpeg public headers retain their own license.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <exception>
#include <mutex>
extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/dict.h>
}

// Resolve only the exact Qt-supplied DLLs; no executable or PATH lookup.
#define FUNCTIONS(X) \
 X(avformat_version) X(avcodec_version) X(avutil_version) \
 X(avformat_alloc_context) X(avformat_open_input) X(avformat_find_stream_info) X(avformat_close_input) \
 X(avformat_alloc_output_context2) X(avformat_new_stream) X(avformat_free_context) \
 X(avcodec_parameters_copy) X(avcodec_parameters_from_context) \
 X(avcodec_find_encoder_by_name) X(avcodec_alloc_context3) X(avcodec_open2) X(avcodec_free_context) \
 X(avio_open2) X(avio_closep) X(avformat_write_header) X(av_read_frame) \
 X(av_interleaved_write_frame) X(av_write_trailer) X(av_packet_alloc) X(av_packet_free) \
 X(av_packet_unref) X(av_new_packet) X(av_packet_rescale_ts) X(av_dict_set) \
 X(av_strerror) X(av_mallocz) X(av_rescale_q)
#define DECLARE(name) decltype(&name) p_##name = nullptr;
FUNCTIONS(DECLARE)

namespace {
std::mutex init_mutex;
bool initialized = false;
HMODULE modules[3] = {};
void message(char* error, int capacity, const char* value) {
    if (error && capacity > 0) snprintf(error, capacity, "%s", value);
}
struct Caption {
    int64_t start_ms;
    int64_t end_ms;
    const char* text;
};
using Progress = int (*)(int64_t, int64_t);
struct Operation {
    Progress progress;
    int64_t position = 0;
    int64_t duration = 0;
    bool cancelled() const { return progress && progress(position, duration) != 0; }
};
int interrupt(void* opaque) { return static_cast<Operation*>(opaque)->cancelled() ? 1 : 0; }
void check(int code) { if (code < 0) throw code; }
void check_cancelled(const Operation& operation) { if (operation.cancelled()) throw AVERROR_EXIT; }
}

extern "C" __declspec(dllexport) unsigned mm_abi_version() { return 1; }

extern "C" __declspec(dllexport) int mm_initialize(const wchar_t* directory, char* error, int capacity) {
    std::lock_guard<std::mutex> lock(init_mutex);
    if (initialized) return 0;
    const wchar_t* names[] = {L"avutil-59.dll", L"avcodec-61.dll", L"avformat-61.dll"};
    wchar_t path[4096];
    int result = -1;
    for (int i = 0; i < 3; ++i) {
        if (!directory || swprintf_s(path, L"%s\\%s", directory, names[i]) < 0) {
            message(error, capacity, "Invalid Qt library directory");
            goto fail;
        }
        modules[i] = LoadLibraryExW(path, nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (!modules[i]) {
            if (error && capacity > 0) snprintf(error, capacity, "Cannot load Qt FFmpeg DLL (Windows error %lu)", GetLastError());
            goto fail;
        }
    }
#define RESOLVE(name) \
    for (HMODULE module : modules) { if (!p_##name) p_##name = reinterpret_cast<decltype(p_##name)>(GetProcAddress(module, #name)); } \
    if (!p_##name) { message(error, capacity, "Missing FFmpeg function: " #name); goto fail; }
    FUNCTIONS(RESOLVE)
    if ((p_avformat_version() >> 16) != LIBAVFORMAT_VERSION_MAJOR ||
        (p_avcodec_version() >> 16) != LIBAVCODEC_VERSION_MAJOR ||
        (p_avutil_version() >> 16) != LIBAVUTIL_VERSION_MAJOR) {
        message(error, capacity, "Qt FFmpeg ABI does not match the media mux component");
        goto fail;
    }
    initialized = true;
    return 0;
fail:
    for (auto& module : modules) { if (module) FreeLibrary(module); module = nullptr; }
#define RESET(name) p_##name = nullptr;
    FUNCTIONS(RESET)
    return result;
}

extern "C" __declspec(dllexport) int mm_mux(const char* input_path, const char* output_path,
    const char* container, const Caption* captions, int count, Progress progress, char* error, int capacity) {
    if (!initialized) { message(error, capacity, "Media mux component is not initialized"); return -1; }
    if (!input_path || !output_path || !container || count < 0 || (count && !captions) ||
        (strcmp(container, "mp4") != 0 && strcmp(container, "matroska") != 0)) {
        message(error, capacity, "Invalid mux parameters"); return -1;
    }
    int64_t previous_end = 0;
    for (int i = 0; i < count; ++i) {
        if (!captions[i].text || !*captions[i].text || strlen(captions[i].text) > 65535 ||
            captions[i].start_ms < previous_end || captions[i].end_ms <= captions[i].start_ms ||
            captions[i].end_ms > INT64_MAX / 1000) {
            message(error, capacity, "Invalid subtitle text or timeline"); return -1;
        }
        previous_end = captions[i].end_ms;
    }
    AVFormatContext *input = nullptr, *output = nullptr;
    AVCodecContext* subtitle_context = nullptr;
    AVPacket *packet = nullptr, *subtitle = nullptr;
    AVStream* subtitle_stream = nullptr;
    Operation operation{progress};
    AVIOInterruptCB interrupt_callback{interrupt, &operation};
    const bool mp4 = strcmp(container, "mp4") == 0;
    int result = 0, caption_index = 0;
    auto write_caption = [&]() {
        check_cancelled(operation);
        const Caption& caption = captions[caption_index];
        int length = static_cast<int>(strlen(caption.text));
        check(p_av_new_packet(subtitle, length + (mp4 ? 2 : 0)));
        if (mp4) { subtitle->data[0] = static_cast<uint8_t>(length >> 8); subtitle->data[1] = static_cast<uint8_t>(length); }
        memcpy(subtitle->data + (mp4 ? 2 : 0), caption.text, length);
        subtitle->pts = subtitle->dts = caption.start_ms;
        subtitle->duration = caption.end_ms - caption.start_ms;
        subtitle->stream_index = subtitle_stream->index;
        subtitle->flags = AV_PKT_FLAG_KEY;
        p_av_packet_rescale_ts(subtitle, AVRational{1, 1000}, subtitle_stream->time_base);
        check(p_av_interleaved_write_frame(output, subtitle));
        ++caption_index;
    };
    try {
        check_cancelled(operation);
        input = p_avformat_alloc_context();
        if (!input) throw AVERROR(ENOMEM);
        input->interrupt_callback = interrupt_callback;
        check(p_avformat_open_input(&input, input_path, nullptr, nullptr));
        check(p_avformat_find_stream_info(input, nullptr));
        operation.duration = input->duration > 0 ? input->duration : 0;
        check(p_avformat_alloc_output_context2(&output, nullptr, container, output_path));
        output->interrupt_callback = interrupt_callback;
        // Only the source audio/video tracks are copied; captions supplied here are authoritative.
        for (unsigned i = 0; i < input->nb_streams; ++i) {
            const auto type = input->streams[i]->codecpar->codec_type;
            if (type != AVMEDIA_TYPE_AUDIO && type != AVMEDIA_TYPE_VIDEO) throw AVERROR(EINVAL);
            AVStream* stream = p_avformat_new_stream(output, nullptr);
            if (!stream) throw AVERROR(ENOMEM);
            check(p_avcodec_parameters_copy(stream->codecpar, input->streams[i]->codecpar));
            stream->codecpar->codec_tag = 0;
            stream->time_base = input->streams[i]->time_base;
            stream->avg_frame_rate = input->streams[i]->avg_frame_rate;
            stream->r_frame_rate = input->streams[i]->r_frame_rate;
            stream->sample_aspect_ratio = input->streams[i]->sample_aspect_ratio;
            stream->disposition = input->streams[i]->disposition;
        }
        if (count) {
            const AVCodec* encoder = p_avcodec_find_encoder_by_name(mp4 ? "mov_text" : "srt");
            if (!encoder) throw AVERROR_ENCODER_NOT_FOUND;
            subtitle_stream = p_avformat_new_stream(output, nullptr);
            if (!subtitle_stream) throw AVERROR(ENOMEM);
            subtitle_stream->time_base = AVRational{1, 1000};
            if (mp4) {
                subtitle_context = p_avcodec_alloc_context3(encoder);
                if (!subtitle_context) throw AVERROR(ENOMEM);
                subtitle_context->time_base = AVRational{1, 1000};
                const char* header = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
                    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                    "Style: Default,Arial,36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,2,20,20,30,1\n"
                    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n";
                subtitle_context->subtitle_header_size = static_cast<int>(strlen(header));
                subtitle_context->subtitle_header = static_cast<uint8_t*>(p_av_mallocz(strlen(header) + AV_INPUT_BUFFER_PADDING_SIZE));
                if (!subtitle_context->subtitle_header) throw AVERROR(ENOMEM);
                memcpy(subtitle_context->subtitle_header, header, strlen(header));
                check(p_avcodec_open2(subtitle_context, encoder, nullptr));
                check(p_avcodec_parameters_from_context(subtitle_stream->codecpar, subtitle_context));
            } else {
                subtitle_stream->codecpar->codec_type = AVMEDIA_TYPE_SUBTITLE;
                subtitle_stream->codecpar->codec_id = encoder->id;
            }
            subtitle_stream->disposition = AV_DISPOSITION_DEFAULT;
            check(p_av_dict_set(&subtitle_stream->metadata, "language", "zho", 0));
            check(p_av_dict_set(&subtitle_stream->metadata, "title", "Lecture captions", 0));
        }
        check_cancelled(operation);
        check(p_avio_open2(&output->pb, output_path, AVIO_FLAG_WRITE, &interrupt_callback, nullptr));
        check(p_avformat_write_header(output, nullptr));
        packet = p_av_packet_alloc();
        subtitle = p_av_packet_alloc();
        if (!packet || !subtitle) throw AVERROR(ENOMEM);
        while ((result = p_av_read_frame(input, packet)) >= 0) {
            AVStream* source = input->streams[packet->stream_index];
            int64_t timestamp = packet->dts != AV_NOPTS_VALUE ? packet->dts : packet->pts;
            int64_t us = timestamp == AV_NOPTS_VALUE ? -1 : p_av_rescale_q(timestamp, source->time_base, AVRational{1, 1000000});
            if (us >= 0 && us > operation.position) operation.position = us;
            check_cancelled(operation);
            while (caption_index < count && captions[caption_index].start_ms <= us / 1000) write_caption();
            p_av_packet_rescale_ts(packet, source->time_base, output->streams[packet->stream_index]->time_base);
            packet->pos = -1;
            check(p_av_interleaved_write_frame(output, packet));
            p_av_packet_unref(packet);
        }
        if (result != AVERROR_EOF) check(result);
        while (caption_index < count) write_caption();
        check_cancelled(operation);
        check(p_av_write_trailer(output));
        check_cancelled(operation);
        result = 0;
    } catch (int code) {
        result = code;
        if (error && capacity > 0) p_av_strerror(code, error, capacity);
    } catch (const std::exception& exception) {
        result = AVERROR(ENOMEM);
        message(error, capacity, exception.what());
    } catch (...) {
        result = AVERROR_UNKNOWN;
        message(error, capacity, "Unexpected media mux error");
    }
    p_av_packet_free(&packet);
    p_av_packet_free(&subtitle);
    p_avcodec_free_context(&subtitle_context);
    if (output) {
        if (output->pb) {
            const int close_result = p_avio_closep(&output->pb);
            if (!result && close_result < 0) {
                result = close_result;
                if (error && capacity > 0) p_av_strerror(result, error, capacity);
            }
        }
        p_avformat_free_context(output);
    }
    if (input) p_avformat_close_input(&input);
    return result;
}
