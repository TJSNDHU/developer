from services.vanguard_scanner import scan_text

def names(findings):
    return [f.get('name') for f in findings]

multi = "-----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n"
bare = "-----BEGIN RSA PRIVATE KEY-----"
print('multi_names=', names(scan_text(multi)))
print('bare_names=', names(scan_text(bare)))
assert 'private_key' in names(scan_text(multi)), 'multi-line private key material should fire'
assert 'private_key' not in names(scan_text(bare)), 'bare key header should remain suppressed'
print('OK')
