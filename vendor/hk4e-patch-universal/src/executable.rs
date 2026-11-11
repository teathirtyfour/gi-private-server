//! Windows paths may contain Unicode even when the executable's name is ASCII.
pub fn game_name(path: &str) -> Option<&'static str> {
    let name = path.rsplit(['\\', '/']).next()?;
    // Module contexts retain this name for the process lifetime. Return known
    // static names rather than borrowing the temporary Windows path buffer.
    if name.eq_ignore_ascii_case("GenshinImpact.exe") {
        Some("GenshinImpact.exe")
    } else if name.eq_ignore_ascii_case("YuanShen.exe") {
        Some("YuanShen.exe")
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unicode_windows_installation_paths_are_supported() {
        assert_eq!(game_name(r"C:\Users\玩家\Games\GenshinImpact.exe"), Some("GenshinImpact.exe"));
        assert_eq!(game_name(r"D:\Jeux été\YuanShen.exe"), Some("YuanShen.exe"));
    }

    #[test]
    fn windows_filename_matching_is_case_insensitive() {
        assert_eq!(game_name(r"C:\game\genshinimpact.EXE"), Some("GenshinImpact.exe"));
    }

    #[test]
    fn wine_paths_and_plain_executable_names_are_supported() {
        assert_eq!(game_name("/private/client/GenshinImpact.exe"), Some("GenshinImpact.exe"));
        assert_eq!(game_name("GenshinImpact.exe"), Some("GenshinImpact.exe"));
    }

    #[test]
    fn other_processes_never_match_the_game() {
        for path in ["", "/", "python.exe", r"C:\GenshinImpact.exe\python.exe", "GenshinImpact.exe.old"] {
            assert_eq!(game_name(path), None);
        }
    }
}
