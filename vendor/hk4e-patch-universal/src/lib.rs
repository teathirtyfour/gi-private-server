use std::{sync::RwLock};

use lazy_static::lazy_static;
use windows::Win32::System::Console;
use windows::Win32::System::SystemServices::DLL_PROCESS_ATTACH;
use windows::Win32::{Foundation::HINSTANCE, System::LibraryLoader::GetModuleFileNameW};
use std::sync::OnceLock;
use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS};
use windows::Win32::System::Threading::{CreateMutexW, GetCurrentProcessId};

// Keep diagnostics available when Wine puts AllocConsole in a separate window.
// The isolated launcher sets the working directory to the development client.
macro_rules! println {
    ($($arg:tt)*) => {{
        std::println!($($arg)*);
        if let Ok(mut log) = std::fs::OpenOptions::new()
            .create(true).append(true).open("private-server-patch.log") {
            use std::io::Write;
            let _ = writeln!(log, $($arg)*);
        }
    }};
}

mod endpoint;
mod executable;
mod interceptor;
mod marshal;
mod modules;
mod util;

use crate::modules::{MhyContext, ModuleManager,
                     CcpBlocker, Http, Misc, Security, WinHttp};

// Unity loads both the root and Plugins copies of this replacement DLL. Rust
// statics are separate in those two images; a Rust Once alone cannot prevent
// them from hooking the same process-wide API entry points twice.
static PATCH_INIT_MUTEX: OnceLock<isize> = OnceLock::new();

unsafe fn claim_patch_initialization() -> bool {
    let name = util::wide_str(&format!("Local\\hk4e-private-patch-init-{}", GetCurrentProcessId()));
    let handle = match CreateMutexW(None, false, PCWSTR(name.as_ptr())) {
        Ok(handle) => handle,
        Err(error) => {
            println!("Patch initialization guard failed: {error}; no hooks installed");
            return false;
        }
    };
    // Read this immediately: subsequent Win32 calls may replace the error code.
    if GetLastError() == ERROR_ALREADY_EXISTS {
        let _ = CloseHandle(handle);
        println!("Private-server patch already initialized by another DLL copy; skipping duplicate hooks");
        return false;
    }
    // Keep the kernel object alive for the process lifetime. The module manager
    // likewise retains its hooks until process exit; do not permit re-hooking.
    PATCH_INIT_MUTEX.set(handle.0).expect("Patch initializer entered twice in one DLL");
    true
}

unsafe fn thread_func() {
    // Identify the process before changing APIs or memory protections. The ANSI
    // API plus UTF-8 unwrap rejected ordinary Unicode Windows user/game paths.
    let mut buffer = vec![0u16; 32768];
    let length = GetModuleFileNameW(None, &mut buffer) as usize;
    if length == 0 || length >= buffer.len() {
        println!("Unable to identify the current executable; no hooks installed");
        return;
    }
    let exe_path = String::from_utf16_lossy(&buffer[..length]);
    let Some(exe_name) = executable::game_name(&exe_path) else {
        println!("Executable is not Genshin. Skipping initialization.");
        return;
    };
    if !claim_patch_initialization() {
        return;
    }
    println!("Private-server patch worker started");
    let mut module_manager = MODULE_MANAGER.write().unwrap();

    // Block query_security_file ASAP
    module_manager.enable(MhyContext::<CcpBlocker>::new(""));
    println!("Dispatch fallback blocker enabled");

    match endpoint::initialize() {
        Ok(target) => println!("Private server dispatch: {}", target.origin),
        Err(error) => {
            println!("Invalid PRIVATE_SERVER_URL: {error}; dispatch hooks not installed");
            return;
        }
    }

    util::disable_memprotect_guard();
    println!("Memory protection wrapper restored");
    if std::env::var("PRIVATE_SERVER_DEBUG_CONSOLE").as_deref() == Ok("1") {
        // Diagnostics must not prevent initialization if a console already exists.
        let _ = Console::AllocConsole();
    }

    println!("Private server client patch (supported build: OSRELWin7.2.0)\nBased on xeondev, modified by Kitsxki_");

    println!("Current executable name: {}", exe_name);

    println!("Initializing modules...");

    if !marshal::find(exe_name) {
        println!("Required string marshal is unavailable; dispatch/security callbacks not installed");
        return;
    }
    module_manager.enable(MhyContext::<Security>::new(exe_name));
    module_manager.enable(MhyContext::<Http>::new(&exe_name));
    module_manager.enable(MhyContext::<Misc>::new(&exe_name));
    module_manager.enable(MhyContext::<WinHttp>::new(&exe_name));

    println!("Successfully initialized!");
}

lazy_static! {
    static ref MODULE_MANAGER: RwLock<ModuleManager> = RwLock::new(ModuleManager::default());
}

// The SDK calls these optional telemetry switches through the Astrolabe DLL.
// Keep the expected ABI in this replacement so those calls do not throw managed
// EntryPointNotFoundException during startup.
#[no_mangle]
#[allow(non_snake_case)]
pub extern "system" fn Astrolabe_SetLrsag(_value: i32) {}

#[no_mangle]
#[allow(non_snake_case)]
pub extern "system" fn Astrolabe_SetFtcSwitch(_value: i32) {}

#[no_mangle]
#[allow(non_snake_case)]
unsafe extern "system" fn DllMain(_: HINSTANCE, call_reason: u32, _: *mut ()) -> bool {
    if call_reason == DLL_PROCESS_ATTACH {
        println!("Private-server patch DLL attached");
        #[cfg(debug_assertions)]
        {
            thread_func();
        }
        #[cfg(not(debug_assertions))]
        {
            std::thread::spawn(|| thread_func());
        }
    }

    true
}
