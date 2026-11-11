use std::ffi::CStr;
use std::sync::OnceLock;
use crate::util;

const PTR_TO_STRING_ANSI: &str = "0F 1F 80 00 00 00 00 E9 ?? ?? ?? ?? 66 66 2E 0F 1F 84 00 00 00 00 00 56 48 83 EC 20 48 85 C9";
const PTR_TO_STRING_ANSI_OFFSET: usize = 0x17;
type MarshalPtrToStringAnsi = unsafe extern "system" fn(*const u8) -> *const u8;
static PTR_TO_STRING_ANSI_ADDR: OnceLock<usize> = OnceLock::new();

pub unsafe fn ptr_to_string_ansi(content: &CStr) -> *const u8 {
    let address = *PTR_TO_STRING_ANSI_ADDR.get()
        .expect("String marshal must be initialized before installing callbacks");
    let func = std::mem::transmute::<usize, MarshalPtrToStringAnsi>(address);
    func(content.to_bytes_with_nul().as_ptr())
}

pub unsafe fn find(module: &str) -> bool {
    if PTR_TO_STRING_ANSI_ADDR.get().is_some() { return true; }
    let ptr_to_string_ansi = util::pattern_scan_il2cpp(module, PTR_TO_STRING_ANSI);
    if let Some(addr) = ptr_to_string_ansi {
        let addr_offset = addr as usize + PTR_TO_STRING_ANSI_OFFSET;
        let _ = PTR_TO_STRING_ANSI_ADDR.set(addr_offset);
        println!("ptr_to_string_ansi: {:x}", addr_offset);
        true
    } else {
        println!("Failed to find ptr_to_string_ansi");
        false
    }
}
