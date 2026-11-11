//! One dispatch origin shared by managed HTTP and native SDK hooks.
//! No Windows dependencies, so validation can run on a build host without a game.
use std::net::Ipv6Addr;
use std::sync::OnceLock;

pub const DEFAULT_URL: &str = "http://127.0.0.1:8088";

#[derive(Debug, PartialEq, Eq)]
pub struct Endpoint {
    pub origin: String,
    pub host: String,
    pub port: u16,
    pub secure: bool,
}

impl Endpoint {
    pub fn parse(value: &str) -> Result<Self, String> {
        let (secure, authority) = if let Some(a) = value.strip_prefix("https://") {
            (true, a)
        } else if let Some(a) = value.strip_prefix("http://") {
            (false, a)
        } else {
            return Err("Server URL must start with http:// or https://".into());
        };
        let authority = authority.strip_suffix('/').unwrap_or(authority);
        if authority.is_empty() || !authority.is_ascii()
            || authority.bytes().any(|b| b.is_ascii_control() || b.is_ascii_whitespace())
            || authority.contains(['/', '?', '#', '@', '\\', '%']) {
            return Err("Server URL must contain only a host and optional port (no credentials or path)".into());
        }
        let (host, port_text) = if let Some(ip) = authority.strip_prefix('[') {
            let (ip, rest) = ip.split_once(']').ok_or("Unclosed IPv6 address")?;
            ip.parse::<Ipv6Addr>().map_err(|_| "Invalid IPv6 address")?;
            let port = if rest.is_empty() { None } else {
                Some(rest.strip_prefix(':').ok_or("Invalid IPv6 port separator")?)
            };
            (ip, port)
        } else {
            let (host, port) = match authority.split_once(':') {
                Some((host, port)) => (host, Some(port)),
                None => (authority, None),
            };
            if host.len() > 253 || host.split('.').any(|label|
                label.is_empty() || label.len() > 63 || label.starts_with('-') || label.ends_with('-')
                || !label.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')) {
                return Err("Invalid server hostname; IPv6 addresses must be bracketed".into());
            }
            (host, port)
        };
        let port = match port_text {
            Some(text) if !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit()) =>
                text.parse::<u16>().map_err(|_| "Port must be between 1 and 65535")?,
            Some(_) => return Err("Invalid port".into()),
            None => if secure { 443 } else { 80 },
        };
        if port == 0 { return Err("Port must be between 1 and 65535".into()); }
        Ok(Self { origin: format!("{}://{}", if secure { "https" } else { "http" }, authority),
            host: host.to_ascii_lowercase(), port, secure })
    }

    pub fn redirect(&self, url: &str) -> Option<String> {
        let authority = url.strip_prefix("https://").or_else(|| url.strip_prefix("http://"))?;
        let suffix = authority.find(['/', '?', '#']).map(|i| &authority[i..]).unwrap_or("");
        Some(format!("{}{}", self.origin, suffix))
    }

    pub fn request_flags(&self, flags: u32) -> u32 {
        const SECURE: u32 = 0x0080_0000;
        if self.secure { flags | SECURE } else { flags & !SECURE }
    }
}

static CONFIG: OnceLock<Result<Endpoint, String>> = OnceLock::new();

pub fn initialize() -> Result<&'static Endpoint, &'static str> {
    CONFIG.get_or_init(|| match std::env::var("PRIVATE_SERVER_URL") {
        Ok(value) => Endpoint::parse(&value),
        Err(std::env::VarError::NotPresent) => Endpoint::parse(DEFAULT_URL),
        Err(_) => Err("PRIVATE_SERVER_URL must be valid Unicode".into()),
    }).as_ref().map_err(String::as_str)
}

pub fn get() -> &'static Endpoint {
    initialize().expect("Dispatch endpoint must be validated before installing hooks")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_local_default_is_unchanged() {
        let e = Endpoint::parse(DEFAULT_URL).unwrap();
        assert_eq!(e.host, "127.0.0.1"); assert_eq!(e.port, 8088); assert!(!e.secure);
    }

    #[test]
    fn public_https_uses_tls_and_default_port() {
        let e = Endpoint::parse("https://play.example.org/").unwrap();
        assert_eq!(e.origin, "https://play.example.org"); assert_eq!(e.port, 443);
        assert_eq!(e.request_flags(0x40), 0x0080_0040);
    }

    #[test]
    fn http_removes_tls_without_losing_other_flags() {
        assert_eq!(Endpoint::parse(DEFAULT_URL).unwrap().request_flags(0x0080_0040), 0x40);
    }

    #[test]
    fn supports_nonstandard_port_and_ipv6() {
        let e = Endpoint::parse("https://[2001:db8::1]:8443").unwrap();
        assert_eq!(e.host, "2001:db8::1"); assert_eq!(e.port, 8443);
        assert_eq!(e.redirect("http://old/path?a=1"), Some("https://[2001:db8::1]:8443/path?a=1".into()));
    }

    #[test]
    fn rejects_ambiguous_or_credential_bearing_urls() {
        for value in ["", "ftp://host", "http://", "http://user:pass@host", "http://host/path",
            "http://host?x=1", "http://host#x", "http://host:0", "http://host:65536", "http://host:",
            "http://host:-1", "http://host:abc", "http://host\\other", "http://host\n",
            "http://[oops]", "http://[::1]suffix", "http://::1", "http://-bad.org", "http://a..b",
            "http://host//", "http://host%00", "http://héllo", "http://host name"] {
            assert!(Endpoint::parse(value).is_err(), "accepted {value:?}");
        }
    }

    #[test]
    fn preserves_entire_request_suffix() {
        let e = Endpoint::parse(DEFAULT_URL).unwrap();
        for suffix in ["", "/", "/account/login?x=a%2Fb&y=2", "?a=1", "/web/#/route"] {
            assert_eq!(e.redirect(&format!("https://old.example{suffix}")), Some(format!("{DEFAULT_URL}{suffix}")));
        }
        assert_eq!(e.redirect("file:///local"), None);
    }
}
