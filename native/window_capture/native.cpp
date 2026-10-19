// Project code, MIT license. Windows Graphics Capture with explicit cursor control.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <stdexcept>

using namespace winrt;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;

namespace {
struct Wake {
    HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    std::atomic_bool closed{false};
    Wake() { if (!event) throw_last_error(); }
    ~Wake() { CloseHandle(event); }
};

struct Capture {
    HWND hwnd;
    com_ptr<ID3D11Device> device;
    com_ptr<ID3D11DeviceContext> context;
    com_ptr<ID3D11Texture2D> staging;
    IDirect3DDevice rt_device{nullptr};
    GraphicsCaptureItem item{nullptr};
    Direct3D11CaptureFramePool pool{nullptr};
    GraphicsCaptureSession session{nullptr};
    Direct3D11CaptureFrame pending{nullptr};
    winrt::Windows::Graphics::SizeInt32 size{};
    std::shared_ptr<Wake> wake = std::make_shared<Wake>();
    event_token frame_token{}, close_token{};
    bool frame_subscribed = false, close_subscribed = false;

    explicit Capture(HWND window) : hwnd(window) {}
    void start(bool cursor) {
        if (!IsWindow(hwnd)) throw std::runtime_error("The capture window no longer exists");
        if (!GraphicsCaptureSession::IsSupported()) throw hresult_not_implemented();
        check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
            device.put(), nullptr, context.put()));
        com_ptr<IInspectable> inspectable;
        check_hresult(CreateDirect3D11DeviceFromDXGIDevice(
            device.as<IDXGIDevice>().get(), inspectable.put()));
        rt_device = inspectable.as<IDirect3DDevice>();
        auto interop = get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        check_hresult(interop->CreateForWindow(hwnd, guid_of<GraphicsCaptureItem>(), put_abi(item)));
        size = item.Size();
        if (size.Width <= 0 || size.Height <= 0) throw std::runtime_error("Empty capture window");
        pool = Direct3D11CaptureFramePool::CreateFreeThreaded(rt_device,
            DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size);
        session = pool.CreateCaptureSession(item);
        if (auto cursor_session = session.try_as<IGraphicsCaptureSession2>()) {
            cursor_session.IsCursorCaptureEnabled(cursor);
            if (cursor_session.IsCursorCaptureEnabled() != cursor)
                throw std::runtime_error("Cursor capture setting was rejected");
        } else if (!cursor) {
            throw hresult_not_implemented(L"Cursor exclusion requires Windows 10 version 2004 or later");
        }
        if (auto border_session = session.try_as<IGraphicsCaptureSession3>()) {
            try { border_session.IsBorderRequired(false); } catch (hresult_error const&) {}
        }
        auto notification = wake;
        frame_token = pool.FrameArrived([notification](auto const&, auto const&) {
            SetEvent(notification->event);
        });
        frame_subscribed = true;
        close_token = item.Closed([notification](auto const&, auto const&) {
            notification->closed = true;
            SetEvent(notification->event);
        });
        close_subscribed = true;
        session.StartCapture();
    }

    ~Capture() {
        // Callbacks retain their own event reference until any in-flight callback ends.
        try { if (frame_subscribed) pool.FrameArrived(frame_token); } catch (...) {}
        try { if (close_subscribed) item.Closed(close_token); } catch (...) {}
        try { if (pending) pending.Close(); } catch (...) {}
        try { if (session) session.Close(); } catch (...) {}
        try { if (pool) pool.Close(); } catch (...) {}
    }

    int read(uint8_t* output, int capacity, int* width, int* height) {
        if (wake->closed || !IsWindow(hwnd)) throw std::runtime_error("The capture window was closed");
        if (!pending) {
            WaitForSingleObject(wake->event, 100);
            if (wake->closed || !IsWindow(hwnd)) throw std::runtime_error("The capture window was closed");
            // Keep the latest frame, so UI or encoder delays do not accumulate a backlog.
            while (auto frame = pool.TryGetNextFrame()) {
                if (pending) pending.Close();
                pending = std::move(frame);
            }
        }
        if (!pending) return 0;
        auto next_size = pending.ContentSize();
        if (next_size.Width <= 0 || next_size.Height <= 0) {
            pending.Close(); pending = nullptr;
            return 0;
        }
        if (next_size.Width != size.Width || next_size.Height != size.Height) {
            pending.Close(); pending = nullptr;
            size = next_size;
            staging = nullptr;
            pool.Recreate(rt_device, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size);
            return 0;
        }
        const int64_t required = int64_t(size.Width) * size.Height * 4;
        if (required > INT32_MAX) throw std::runtime_error("Capture frame is too large");
        *width = size.Width; *height = size.Height;
        if (!output || capacity < required) return 2; // Retain pending until caller allocates.
        auto access = pending.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
        com_ptr<ID3D11Texture2D> texture;
        check_hresult(access->GetInterface(__uuidof(ID3D11Texture2D), texture.put_void()));
        if (!staging) {
            D3D11_TEXTURE2D_DESC desc{};
            desc.Width = static_cast<UINT>(size.Width);
            desc.Height = static_cast<UINT>(size.Height);
            desc.MipLevels = desc.ArraySize = 1;
            desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
            desc.SampleDesc.Count = 1;
            desc.Usage = D3D11_USAGE_STAGING;
            desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            check_hresult(device->CreateTexture2D(&desc, nullptr, staging.put()));
        }
        D3D11_BOX region{0, 0, 0, static_cast<UINT>(size.Width), static_cast<UINT>(size.Height), 1};
        context->CopySubresourceRegion(staging.get(), 0, 0, 0, 0, texture.get(), 0, &region);
        D3D11_MAPPED_SUBRESOURCE mapped{};
        check_hresult(context->Map(staging.get(), 0, D3D11_MAP_READ, 0, &mapped));
        const size_t stride = size_t(size.Width) * 4;
        for (int row = 0; row < size.Height; ++row)
            std::memcpy(output + size_t(row) * stride,
                static_cast<const uint8_t*>(mapped.pData) + size_t(row) * mapped.RowPitch, stride);
        context->Unmap(staging.get(), 0);
        pending.Close(); pending = nullptr;
        return 1;
    }
};

void error_text(char* error, int capacity, const char* text) {
    if (error && capacity > 0) std::snprintf(error, capacity, "%s", text);
}
void current_error(char* error, int capacity) {
    try { throw; }
    catch (hresult_error const& ex) {
        char text[1024];
        std::snprintf(text, sizeof(text), "0x%08X: %s", static_cast<unsigned>(ex.code().value),
                      to_string(ex.message()).c_str());
        error_text(error, capacity, text);
    }
    catch (std::exception const& ex) { error_text(error, capacity, ex.what()); }
    catch (...) { error_text(error, capacity, "Unknown window capture error"); }
}
}

// All operations on a handle must occur on the same worker thread.
extern "C" __declspec(dllexport) void* wc_open(HWND hwnd, int cursor, char* error, int capacity) {
    bool apartment = false;
    try {
        init_apartment(apartment_type::multi_threaded);
        apartment = true;
        auto capture = std::make_unique<Capture>(hwnd);
        capture->start(cursor != 0);
        return capture.release();
    } catch (...) {
        current_error(error, capacity);
        if (apartment) uninit_apartment();
        return nullptr;
    }
}
extern "C" __declspec(dllexport) int wc_read(void* handle, uint8_t* output, int capacity,
    int* width, int* height, char* error, int error_capacity) {
    if (!handle || !width || !height) return -1;
    try { return static_cast<Capture*>(handle)->read(output, capacity, width, height); }
    catch (...) { current_error(error, error_capacity); return -1; }
}
extern "C" __declspec(dllexport) void wc_close(void* handle) {
    if (!handle) return;
    delete static_cast<Capture*>(handle);
    uninit_apartment();
}
