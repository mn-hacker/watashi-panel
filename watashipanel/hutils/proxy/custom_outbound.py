# watashi v12.3.55dev: the builder and tester of the hand built outbounds.
"""Turns a CustomOutbound row into the dict xray or sing-box expects in its
`outbounds` array, and renders the whole test config the commander runs before
anything is saved — the same round trip the inbounds take.

Validation lives here too, so the admin page and a restored backup describe a
row the same way: the protocol decides the core, the tag must be unique inside
that core and can never take a reserved name, and every protocol picks the
fields it needs from the settings JSON (a missing field is filled with the
default, never with an error, so a hand edited backup still loads).
"""
import json

from watashipanel.models.custom_outbound import (
    CustomOutbound, RESERVED_TAGS, XRAY_OUTBOUND_PROTOCOLS, SINGBOX_PROTOCOLS_FULL,
)


def _(text, **kw):
    return text


def ws_suggest_tag(row: CustomOutbound) -> str:
    """A tag from the remark or the address, unique in its core."""
    base = ''.join(c for c in (row.remark or row.address or 'out').lower() if c.isalnum())[:20] or 'out'
    tag, n = base, 2
    taken = {r.tag_name for r in CustomOutbound.all_rows() if r.id != row.id}
    while tag in taken or tag in RESERVED_TAGS:
        tag, n = f'{base}{n}', n + 1
    return tag


def ws_validate(row: CustomOutbound) -> list[str]:
    """The problems that must block a save, as translated message keys."""
    problems = []
    if not row.address or '.' not in row.address and not row.address.startswith('['):
        problems.append('outbound.address.required')
    if not (1 <= int(row.port or 0) <= 65535):
        problems.append('outbound.port.range')
    if row.protocol in ('hysteria2', 'tuic', 'shadowtls', 'ss2022'):
        # sing-box only: the xray core cannot serve these, the page says so.
        problems.append('outbound.protocol.xray_only')
    if row.network == 'udp' and row.protocol != 'wireguard':
        problems.append('outbound.network.bad')
    if row.core == 'singbox' and row.protocol not in SINGBOX_PROTOCOLS_FULL:
        problems.append('outbound.protocol.core')
    if not row.tag_name:
        problems.append('outbound.tag.required')
    elif row.tag_name in RESERVED_TAGS:
        problems.append('outbound.tag.reserved')
    else:
        taken = [r for r in CustomOutbound.all_rows() if r.tag_name == row.tag_name and r.id != row.id]
        if taken:
            problems.append('outbound.tag.taken')
    if row.security not in ('none', 'tls', 'reality'):
        problems.append('outbound.security.bad')
    if row.network not in ('tcp', 'ws', 'grpc', 'httpupgrade', 'xhttp', 'http', 'quic'):
        problems.append('outbound.network.bad')
    return problems


def _tls_block(row: CustomOutbound) -> dict:
    o = row.opts
    block = {'enabled': row.security in ('tls', 'reality')}
    if block['enabled']:
        sni = o.get('sni') or row.address
        block['serverName'] = sni
        if o.get('insecure'):
            block['allowInsecure'] = True
        if o.get('fingerprint'):
            block['fingerprint'] = o['fingerprint']
        if o.get('alpn'):
            block['alpn'] = [a.strip() for a in str(o['alpn']).split(',') if a.strip()]
        if row.security == 'reality':
            block['reality'] = {'enabled': True, 'publicKey': o.get('pbk', ''), 'shortId': o.get('sid', '')}
            block.pop('serverName', None) and block.setdefault('serverName', sni)
    return block


def _transport_block(row: CustomOutbound) -> dict | None:
    o = row.opts
    net = row.network
    if net == 'tcp':
        return None
    if net == 'ws':
        return {'type': 'ws', 'path': o.get('path', '/'), 'headers': ({'Host': o['host']} if o.get('host') else None)}
    if net == 'grpc':
        return {'type': 'grpc', 'serviceName': o.get('serviceName', '')}
    if net == 'httpupgrade':
        return {'type': 'httpupgrade', 'path': o.get('path', '/'), 'host': o.get('host', '')}
    if net in ('xhttp', 'http'):
        return {'type': 'http', 'path': o.get('path', '/'), 'host': [o['host']] if o.get('host') else None}
    if net == 'quic':
        return {'type': 'quic'}
    return None


def to_xray(row: CustomOutbound) -> dict:
    """The outbound dict of the xray core."""
    o = row.opts
    address, port = row.address, int(row.port)
    proto = row.protocol
    tag = row.tag

    def user(email='', **kw):
        base = {'email': email or f'custom-out-{row.id}'}
        base.update(kw)
        return base

    if proto == 'vless':
        settings = {'vnext': [{'address': address, 'port': port, 'users': [user(id=o.get('uuid', ''), flow=o.get('flow', '') or '')]}], 'address': address}
    elif proto == 'vmess':
        settings = {'vnext': [{'address': address, 'port': port, 'users': [user(id=o.get('uuid', ''), security=o.get('encryption', 'auto'), alterId=int(o.get('alterId', 0) or 0))]}]}
    elif proto == 'trojan':
        settings = {'servers': [{'address': address, 'port': port, 'password': o.get('password', '')}]}
    elif proto == 'shadowsocks':
        settings = {'servers': [{'address': address, 'port': port, 'method': o.get('method', 'aes-256-gcm'), 'password': o.get('password', ''), 'uot': bool(o.get('uot'))}]}
    elif proto == 'socks':
        entry = {'address': address, 'port': port}
        if o.get('username'):
            entry['users'] = [{'user': o['username'], 'pass': o.get('password', '')}]
        settings = {'servers': [entry]}
    elif proto == 'http':
        entry = {'address': address, 'port': port}
        if o.get('username'):
            entry['users'] = [{'user': o['username'], 'pass': o.get('password', '')}]
        settings = {'servers': [entry]}
    elif proto == 'wireguard':
        peers = o.get('peers') or [{'publicKey': o.get('peer_pub', ''), 'allowedIPs': o.get('allowed_ips', ['0.0.0.0/0']), 'endpoint': f'{address}:{port}'}]
        settings = {'secretKey': o.get('secret_key', ''), 'address': (o.get('local_ips') or ['172.16.0.2/32']), 'peers': peers, 'mtu': int(o.get('mtu', 1420) or 1420)}
    elif proto == 'freedom':
        settings = {'domainStrategy': o.get('domainStrategy', 'AsIs')}
    elif proto == 'blackhole':
        settings = {'response': {'type': o.get('response', 'none')}}
    elif proto == 'dns':
        settings = {'network': o.get('network', 'udp'), 'address': address, 'port': port}
    else:
        settings = {}

    out = {'tag': tag, 'protocol': proto, 'settings': settings}
    stream = {}
    if proto in ('vless', 'vmess', 'trojan', 'shadowsocks'):
        tls = _tls_block(row)
        if tls.get('enabled'):
            stream['security'] = 'reality' if row.security == 'reality' else 'tls'
            if row.security == 'reality':
                stream['realitySettings'] = {'enabled': True, 'publicKey': tls['reality']['publicKey'], 'shortId': tls['reality']['shortId'], 'serverName': tls.get('serverName', row.address)}
            else:
                stream['tlsSettings'] = {k: v for k, v in tls.items() if k not in ('enabled', 'reality')}
        transport = _transport_block(row)
        if transport:
            stream['network'] = 'http' if row.network == 'http' else row.network
            key = {'ws': 'wsSettings', 'grpc': 'grpcSettings', 'httpupgrade': 'httpupgradeSettings', 'http': 'httpSettings', 'quic': 'quicSettings'}[transport['type']]
            stream[key] = {k: v for k, v in transport.items() if k != 'type'}
    if stream:
        out['streamSettings'] = stream
    if o.get('mux_enable'):
        out['mux'] = {'enabled': True, 'concurrency': int(o.get('mux_concurrency', 8) or 8)}
    return out


def to_singbox(row: CustomOutbound) -> dict:
    """The outbound dict of the sing-box core."""
    o = row.opts
    address, port = row.address, int(row.port)
    proto = row.protocol
    tag = row.tag
    tls = _tls_block(row)
    tls_used = tls.get('enabled')

    def base(kind, **kw):
        one = {'type': kind, 'tag': tag, 'server': address, 'server_port': port}
        one.update(kw)
        return one

    if proto == 'vless':
        out = base('vless', uuid=o.get('uuid', ''), flow=o.get('flow', '') or '')
    elif proto == 'vmess':
        out = base('vmess', uuid=o.get('uuid', ''), security=o.get('encryption', 'auto'), alter_id=int(o.get('alterId', 0) or 0))
    elif proto == 'trojan':
        out = base('trojan', password=o.get('password', ''))
    elif proto in ('shadowsocks', 'ss2022'):
        out = base('shadowsocks', method=o.get('method', 'aes-256-gcm'), password=o.get('password', ''))
    elif proto == 'socks':
        out = base('socks')
        if o.get('username'):
            out['username'], out['password'] = o['username'], o.get('password', '')
    elif proto == 'http':
        out = base('http')
        if o.get('username'):
            out['username'], out['password'] = o['username'], o.get('password', '')
    elif proto == 'hysteria2':
        out = base('hysteria2', password=o.get('password', ''))
        if o.get('obfs'):
            out['obfs'] = {'type': 'salamander', 'password': o['obfs']}
        if o.get('up_mbps'):
            out['up_mbps'] = int(o['up_mbps'])
        if o.get('down_mbps'):
            out['down_mbps'] = int(o['down_mbps'])
    elif proto == 'tuic':
        out = base('tuic', uuid=o.get('uuid', ''), password=o.get('password', ''), congestion_control=o.get('congestion', 'bbr'), udp_relay_mode=o.get('udp_relay', 'native'))
    elif proto == 'shadowtls':
        out = base('shadowtls', password=o.get('password', ''), version=int(o.get('version', 3) or 3))
    elif proto == 'wireguard':
        peers = o.get('peers') or [{'public_key': o.get('peer_pub', ''), 'allowed_ips': o.get('allowed_ips', ['0.0.0.0/0']), 'endpoint': f'{address}:{port}'}]
        out = base('wireguard', private_key=o.get('secret_key', ''), local_address=(o.get('local_ips') or ['172.16.0.2/32']), peers=peers, mtu=int(o.get('mtu', 1420) or 1420))
    else:
        out = base('direct')

    if tls_used and proto not in ('wireguard',):
        st = {'enabled': True, 'server_name': tls.get('serverName', address), 'insecure': bool(tls.get('allowInsecure'))}
        if tls.get('fingerprint'):
            st['utls'] = {'enabled': True, 'fingerprint': tls['fingerprint']}
        if row.security == 'reality':
            st['reality'] = {'enabled': True, 'public_key': tls['reality']['publicKey'], 'short_id': tls['reality']['shortId']}
        if tls.get('alpn'):
            st['alpn'] = tls['alpn']
        out['tls'] = st
    transport = _transport_block(row)
    if transport and proto in ('vless', 'vmess', 'trojan'):
        t = {'type': transport['type']}
        if transport.get('path'):
            t['path'] = transport['path']
        if transport.get('headers'):
            t['headers'] = transport['headers']
        if transport.get('serviceName'):
            t['service_name'] = transport['serviceName']
        if transport.get('host'):
            t['host'] = transport['host']
        out['transport'] = t
    if o.get('mux_enable') and proto in ('vless', 'vmess', 'trojan'):
        out['multiplex'] = {'enabled': True, 'protocol': o.get('mux_protocol', 'h2mux'), 'max_connections': int(o.get('mux_concurrency', 4) or 4)}
    return out


def ws_export_row(row: CustomOutbound) -> dict:
    """The row for the active core (the commander picks the right one)."""
    return to_singbox(row) if row.core == 'singbox' else to_xray(row)


def ws_export_rows(rows: list[CustomOutbound] | None = None, core: str = 'xray', quiet: bool = False) -> list[dict]:
    rows = CustomOutbound.all_rows() if rows is None else rows
    maker = to_singbox if core == 'singbox' else to_xray
    out = []
    for row in rows:
        if not row.enable or row.core != core:
            continue
        try:
            out.append(maker(row))
        except Exception as problem:
            if not quiet:
                print('custom outbound skipped', row.remark, problem)
    return out


def ws_rev(rows: list[dict], core: str = 'xray') -> str:
    """The fingerprint of the set one core serves, the way custom.py does it:
    the same set asks for the same apply again and again no more."""
    import hashlib
    payload = json.dumps([r for r in rows], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f'{core}|{payload}'.encode()).hexdigest()[:16]


def ws_test_file(rows: list[dict], core: str = 'xray') -> dict:
    """The whole test config, one stand-in inbound, for the commander."""
    if core == 'singbox':
        return {
            'log': {'level': 'error'},
            'inbounds': [{'type': 'socks', 'tag': 'test-in', 'listen': '127.0.0.1', 'listen_port': 39021}],
            'outbounds': rows + [{'type': 'direct', 'tag': 'direct'}],
        }
    return {
        'log': {'loglevel': 'error'},
        'inbounds': [{'tag': 'test-in', 'listen': '127.0.0.1', 'port': 39020, 'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': rows + [{'tag': 'direct', 'protocol': 'freedom'}],
    }
