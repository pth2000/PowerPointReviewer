// Application-specific PCM16 C API; distributed under the application's MIT license.
#include "signalsmith-stretch.h"
#include <cstdint>
#include <cmath>
#include <climits>
#include <new>

extern "C" __declspec(dllexport) uint32_t ss_abi_version() noexcept {
    return 1;
}

// All buffers belong to the caller; no allocations cross the DLL boundary.
// Status: 0 success, 1 invalid arguments, 2 allocation failure, 3 processing failure.
extern "C" __declspec(dllexport) int ss_process_pcm16(
        const int16_t *input, uint32_t frames, uint32_t sampleRate,
        uint32_t channels, double speed, int16_t *output,
        uint32_t outputCapacity, uint32_t *writtenFrames) noexcept {
    if (!writtenFrames) return 1;
    *writtenFrames = 0;
    if (!input || !output || !frames || frames > INT_MAX ||
            channels < 1 || channels > 2 || sampleRate < 1000 || sampleRate > 384000 ||
            !std::isfinite(speed) || speed < 0.5 || speed > 2) return 1;
    const double roundedFrames = std::round(frames / speed);
    if (roundedFrames < 1 || roundedFrames > INT_MAX || roundedFrames > outputCapacity) return 1;
    const int outputFrames = static_cast<int>(roundedFrames);
    try {
        if (speed == 1) {
            std::copy_n(input, size_t(frames) * channels, output);
            *writtenFrames = frames;
            return 0;
        }
        // Independent state and a fixed algorithm seed for every clip.
        signalsmith::stretch::SignalsmithStretch<float> processor(0);
        processor.presetDefault(int(channels), float(sampleRate));
        std::vector<std::vector<float>> inputs(channels, std::vector<float>(frames));
        std::vector<std::vector<float>> outputs(channels, std::vector<float>(outputFrames));
        std::vector<float *> inputPointers(channels), outputPointers(channels);
        for (uint32_t c = 0; c < channels; ++c) {
            for (uint32_t i = 0; i < frames; ++i) {
                inputs[c][i] = input[size_t(i) * channels + c] / 32768.0f;
            }
            inputPointers[c] = inputs[c].data();
            outputPointers[c] = outputs[c].data();
        }
        int outputOffset = 0;
        // The upstream exact() helper aligns both ends of a complete clip.
        if (!processor.exact(inputPointers.data(), int(frames),
                             outputPointers.data(), outputFrames)) {
            // exact() rejects clips shorter than its analysis window. Pad the
            // input and flush the pending output instead of returning silence.
            const int inputLatency = processor.inputLatency();
            const int outputLatency = processor.outputLatency();
            if (frames > uint32_t(INT_MAX - inputLatency) ||
                    outputFrames > INT_MAX - outputLatency) return 1;
            for (uint32_t c = 0; c < channels; ++c) {
                inputs[c].resize(size_t(frames) + inputLatency, 0);
                outputs[c].resize(size_t(outputFrames) + outputLatency, 0);
                inputPointers[c] = inputs[c].data();
                outputPointers[c] = outputs[c].data();
            }
            processor.seek(inputPointers.data(), inputLatency, speed);
            for (uint32_t c = 0; c < channels; ++c) inputPointers[c] += inputLatency;
            processor.process(inputPointers.data(), int(frames), outputPointers.data(), outputFrames);
            for (uint32_t c = 0; c < channels; ++c) outputPointers[c] += outputFrames;
            processor.flush(outputPointers.data(), outputLatency, float(speed));
            outputOffset = outputLatency;
        }
        for (uint32_t c = 0; c < channels; ++c) {
            for (int i = 0; i < outputFrames; ++i) {
                const float sample = outputs[c][size_t(i) + outputOffset];
                if (!std::isfinite(sample)) return 3;
                const double value = std::clamp(std::round(double(sample) * 32768), -32768.0, 32767.0);
                output[size_t(i) * channels + c] = static_cast<int16_t>(value);
            }
        }
        *writtenFrames = outputFrames;
        return 0;
    } catch (const std::bad_alloc &) {
        return 2;
    } catch (...) {
        // C++ exceptions must never escape through ctypes.
        return 3;
    }
}
