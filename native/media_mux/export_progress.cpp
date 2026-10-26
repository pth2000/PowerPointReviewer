// Observe only this export's Media Foundation events. No injection or trace files.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <evntrace.h>
#include <evntcons.h>
#include <tdh.h>
#include <stdint.h>
#include <algorithm>
#include <atomic>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <tuple>
#include <vector>
#include <map>

namespace {
const GUID provider = {0x4b7eac67, 0xfc53, 0x448c, {0xa4,0x9d,0x7c,0xc6,0xdb,0x52,0x4d,0xa7}};
const GUID video_type = {0x73646976,0x0000,0x0010,{0x80,0x00,0x00,0xaa,0x00,0x38,0x9b,0x71}};
std::atomic<bool> local_active{false};

// phase: 0 awaiting samples, 1 processing picture, 2 saving, 3 unavailable.
struct Snapshot {
    int64_t position_us = 0;
    int32_t phase = 0;
    int32_t idle_ms = 0;
    uint32_t error_code = 0;
    uint32_t video_jobs = 0;
};

bool field(PEVENT_RECORD record, const wchar_t* name, void* value, ULONG expected) {
    PROPERTY_DATA_DESCRIPTOR property{};
    property.PropertyName = reinterpret_cast<ULONGLONG>(name);
    property.ArrayIndex = ULONG_MAX;
    ULONG size = 0;
    if (TdhGetPropertySize(record, 0, nullptr, 1, &property, &size) != ERROR_SUCCESS ||
            size != expected) return false;
    return TdhGetProperty(record, 0, nullptr, 1, &property, size,
                          static_cast<PBYTE>(value)) == ERROR_SUCCESS;
}

class Monitor {
    DWORD pid_;
    HANDLE gate_ = nullptr;
    bool owns_gate_ = false;
    bool owns_local_ = false;
    std::wstring name_;
    std::vector<BYTE> storage_;
    TRACEHANDLE session_ = 0;
    TRACEHANDLE consumer_ = INVALID_PROCESSTRACE_HANDLE;
    std::thread reader_;
    std::atomic<bool> closing_{false};
    std::mutex mutex_;
    Snapshot data_;
    ULONGLONG updated_ = GetTickCount64();
    ULONGLONG queried_ = 0;
    std::set<std::pair<uint64_t, ULONG>> active_video_;
    std::set<std::tuple<uint64_t, unsigned, ULONG>> videos_;
    std::map<uint64_t, unsigned> generations_;

    PEVENT_TRACE_PROPERTIES properties() {
        return reinterpret_cast<PEVENT_TRACE_PROPERTIES>(storage_.data());
    }
    void initialize_properties() {
        storage_.assign(sizeof(EVENT_TRACE_PROPERTIES) + (name_.size() + 1) * sizeof(wchar_t), 0);
        auto props = properties();
        props->Wnode.BufferSize = static_cast<ULONG>(storage_.size());
        props->Wnode.Flags = WNODE_FLAG_TRACED_GUID;
        props->Wnode.ClientContext = 1;
        props->BufferSize = 64;
        props->MinimumBuffers = 4;
        props->MaximumBuffers = 16;
        props->LogFileMode = EVENT_TRACE_REAL_TIME_MODE;
        props->FlushTimer = 1;
        props->LoggerNameOffset = sizeof(EVENT_TRACE_PROPERTIES);
    }
    void unavailable(ULONG error) noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        data_.phase = 3;
        data_.error_code = error;
    }
    static void WINAPI receive(PEVENT_RECORD record) noexcept {
        auto self = static_cast<Monitor*>(record->UserContext);
        if (!self || self->closing_ || record->EventHeader.ProcessId != self->pid_ ||
                !IsEqualGUID(record->EventHeader.ProviderId, provider)) return;
        try { self->update(record); }
        catch (...) { self->unavailable(ERROR_INVALID_DATA); }
    }
    void update(PEVENT_RECORD record) {
        auto id = record->EventHeader.EventDescriptor.Id;
        uint64_t object = 0, timestamp = 0;
        ULONG stream = 0, error = 0;
        GUID type{};
        bool valid = field(record, L"Object", &object, sizeof(object));
        // The monitored Office process can be 32-bit even when this DLL is 64-bit.
        if (!valid) {
            ULONG small_object = 0;
            valid = field(record, L"Object", &small_object, sizeof(small_object));
            object = small_object;
        }
        if (id == 100) valid = valid && field(record,L"StreamIndex",&stream,sizeof(stream)) &&
                                     field(record,L"MajorType",&type,sizeof(type));
        if (id == 103) valid = valid && field(record,L"StreamIndex",&stream,sizeof(stream)) &&
                                     field(record,L"Timestamp",&timestamp,sizeof(timestamp));
        if (id == 109) valid = valid && field(record,L"ErrorCode",&error,sizeof(error));
        if (!valid || !object) { unavailable(ERROR_INVALID_DATA); return; }
        std::lock_guard<std::mutex> lock(mutex_);
        if (data_.phase == 3) return;
        const auto identity = std::make_pair(object,stream);
        if (id == 100) {
            active_video_.erase(identity);
            if (IsEqualGUID(type,video_type)) {
                active_video_.insert(identity);
                videos_.insert(std::make_tuple(object,generations_[object],stream));
                data_.video_jobs = static_cast<ULONG>(videos_.size());
                if (videos_.size() > 1) { data_.phase = 3; data_.error_code = ERROR_BUSY; }
            }
        } else if (id == 103 && active_video_.count(identity)) {
            const auto position = static_cast<int64_t>(timestamp / 10);
            if (position > data_.position_us) updated_ = GetTickCount64();
            data_.position_us = (std::max)(data_.position_us, position);
            data_.phase = 1;
        } else if (id == 108 || id == 109 || id == 114 || id == 115) {
            bool video = false;
            for (const auto& key : active_video_) if (key.first == object) video = true;
            if (video) {
                data_.phase = (id == 114 || id == 115 || error) ? 3 : 2;
                data_.error_code = error;
                updated_ = GetTickCount64();
            }
            if (id == 109) {
                for (auto it = active_video_.begin(); it != active_video_.end();) {
                    if (it->first == object) it = active_video_.erase(it); else ++it;
                }
                if (generations_.size() > 128) { data_.phase = 3; return; }
                ++generations_[object];
            }
        }
    }

public:
    explicit Monitor(DWORD pid) : pid_(pid) {}
    ~Monitor() { close(); }
    ULONG start() {
        bool inactive = false;
        if (!local_active.compare_exchange_strong(inactive,true)) return ERROR_BUSY;
        owns_local_ = true;
        DWORD desktop_session = 0;
        if (!ProcessIdToSessionId(GetCurrentProcessId(), &desktop_session)) return GetLastError();
        // One observer per desktop session. Holding the mutex also permits removal of
        // our orphaned ETW session after a previous process crash, never another live observer.
        name_ = L"PowerPointReviewer.VideoExport." + std::to_wstring(desktop_session);
        gate_ = CreateMutexW(nullptr,FALSE,L"Local\\PowerPointReviewer.VideoExport");
        if (!gate_) return GetLastError();
        const auto wait = WaitForSingleObject(gate_,0);
        if (wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED) return ERROR_BUSY;
        owns_gate_ = true;
        initialize_properties();
        ULONG result = StartTraceW(&session_,name_.c_str(),properties());
        if (result == ERROR_ALREADY_EXISTS) {
            result = ControlTraceW(0,name_.c_str(),properties(),EVENT_TRACE_CONTROL_STOP);
            if (result != ERROR_SUCCESS && result != ERROR_WMI_INSTANCE_NOT_FOUND) return result;
            initialize_properties();
            result = StartTraceW(&session_,name_.c_str(),properties());
        }
        if (result) { session_ = 0; return result; }
        EVENT_TRACE_LOGFILEW logfile{};
        logfile.LoggerName = const_cast<LPWSTR>(name_.c_str());
        logfile.ProcessTraceMode = PROCESS_TRACE_MODE_REAL_TIME | PROCESS_TRACE_MODE_EVENT_RECORD;
        logfile.EventRecordCallback = receive;
        logfile.Context = this;
        consumer_ = OpenTraceW(&logfile);
        if (consumer_ == INVALID_PROCESSTRACE_HANDLE) return GetLastError();
        struct Filter { BOOLEAN include; UCHAR reserved; USHORT count; USHORT ids[6]; };
        Filter selected{TRUE,0,6,{100,103,108,109,114,115}};
        EVENT_FILTER_DESCRIPTOR filters[2]{};
        filters[0] = {reinterpret_cast<ULONGLONG>(&pid_),sizeof(pid_),EVENT_FILTER_TYPE_PID};
        filters[1] = {reinterpret_cast<ULONGLONG>(&selected),sizeof(selected),EVENT_FILTER_TYPE_EVENT_ID};
        ENABLE_TRACE_PARAMETERS parameters{};
        parameters.Version = ENABLE_TRACE_PARAMETERS_VERSION_2;
        parameters.EnableFilterDesc = filters;
        parameters.FilterDescCount = 2;
        result = EnableTraceEx2(session_,&provider,EVENT_CONTROL_CODE_ENABLE_PROVIDER,
                               TRACE_LEVEL_INFORMATION,0x4000000000000000ULL,0,0,&parameters);
        if (result) return result;
        auto consumer = consumer_;
        reader_ = std::thread([this,consumer]() mutable {
            auto result = ProcessTrace(&consumer,1,nullptr,nullptr);
            if (!closing_) unavailable(result ? result : ERROR_OPERATION_ABORTED);
        });
        return ERROR_SUCCESS;
    }
    void poll(Snapshot* output) {
        const auto now = GetTickCount64();
        if (now - queried_ >= 1000) {
            queried_ = now;
            auto result = ControlTraceW(session_,name_.c_str(),properties(),EVENT_TRACE_CONTROL_QUERY);
            if (result) unavailable(result);
            else if (properties()->EventsLost || properties()->RealTimeBuffersLost)
                unavailable(ERROR_INVALID_DATA);
        }
        std::lock_guard<std::mutex> lock(mutex_);
        *output = data_;
        output->idle_ms = static_cast<int32_t>((std::min)(now > updated_ ? now - updated_ : 0,2147483647ULL));
    }
    ULONG close() noexcept {
        closing_ = true;
        ULONG result = ERROR_SUCCESS;
        if (session_) {
            result = ControlTraceW(session_,name_.c_str(),properties(),EVENT_TRACE_CONTROL_STOP);
            session_ = 0;
        }
        if (consumer_ != INVALID_PROCESSTRACE_HANDLE) {
            CloseTrace(consumer_);
            consumer_ = INVALID_PROCESSTRACE_HANDLE;
        }
        if (reader_.joinable()) reader_.join();
        if (owns_gate_) { ReleaseMutex(gate_); owns_gate_ = false; }
        if (gate_) { CloseHandle(gate_); gate_ = nullptr; }
        if (owns_local_) { local_active = false; owns_local_ = false; }
        return result;
    }
};
}

// All three functions are called on the export worker thread. The reader never calls Python.
extern "C" __declspec(dllexport) ULONG mm_export_progress_start(DWORD pid, void** handle) noexcept {
    if (!pid || !handle) return ERROR_INVALID_PARAMETER;
    *handle = nullptr;
    try {
        auto monitor = std::make_unique<Monitor>(pid);
        auto result = monitor->start();
        if (result) return result;
        *handle = monitor.release();
        return ERROR_SUCCESS;
    } catch (...) { return ERROR_NOT_ENOUGH_MEMORY; }
}
extern "C" __declspec(dllexport) ULONG mm_export_progress_poll(void* handle, Snapshot* output) noexcept {
    if (!handle || !output) return ERROR_INVALID_PARAMETER;
    try {
        static_cast<Monitor*>(handle)->poll(output);
        return ERROR_SUCCESS;
    } catch (...) { return ERROR_INVALID_DATA; }
}
extern "C" __declspec(dllexport) ULONG mm_export_progress_stop(void* handle) noexcept {
    if (!handle) return ERROR_SUCCESS;
    auto monitor = static_cast<Monitor*>(handle);
    auto result = monitor->close();
    delete monitor;
    return result;
}
