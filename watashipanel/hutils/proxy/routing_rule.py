# watashi v12.3.56dev: the routing rules service (the Routing page, phase 2).
"""Turns the rows of the routing_rule table into the two shapes the cores
understand, validates one row against the vocabulary of its core, and hands the
page the choices it needs. The render templates (xray/configs/03_routing.json.j2
and singbox/configs/03_routing.json.j2) call ws_rules_for_render() so the rules
ride into the core config on every apply, exactly like the custom inbounds and
outbounds do.

An xray rule is the well known {"type": "field", ...} object; a sing-box rule
is its modern spelling (domain_suffix/domain/ip_cidr/port/protocol...). The
builtin target words map per core: block -> blackhole (both), proxy -> the
panel's own proxy outbound, warp -> WARP (left to the render to drop while the
WARP mode is off, the same way the templates already treat WARP)."""
import re

from watashipanel.models.routing_rule import (
    RoutingRule, BUILTIN_TARGETS, NETWORK_CHOICES, PROTOCOL_CHOICES)

PORT_RE = re.compile(r'^\d+(-\d+)?(,\d+(-\d+)?)*$')
# a domain the cores accept in a plain list: a plain name, a suffix dot form,
# a geosite: or domain: prefix from either vocabulary
DOMAIN_RE = re.compile(
    r'^(?:geosite:[\w.-]+|domain:[\w.-]+|ext:[\w.-]+:[\w.-]+|[\w.*-]+(\.[\w*-]+)+)$')
IP_RE = re.compile(
    r'^(?:geoip:[\w.-]+|(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?|::1|[0-9a-f:]+(/\d{1,3})?)$', re.I)


def ws_target_choices(rows: list) -> list:
    """The page's target dropdown: the builtins plus one entry per enabled
    custom outbound tag (the tag the cores actually see, without the prefix)."""
    choices = [{'value': t, 'label': t} for t in BUILTIN_TARGETS]
    for row in rows:
        if getattr(row, 'enable', True) and row.tag_name:
            choices.append({'value': row.tag_name, 'label': f'{row.tag_name} ({row.protocol})'})
    return choices


def ws_validate(rule: RoutingRule) -> list:
    """The problems of one row, as translation keys. Empty on a good row."""
    problems = []
    if not rule.target or (rule.target not in BUILTIN_TARGETS and not rule.target.startswith('custom-out-')):
        problems.append('rule.target.required')
    if rule.core_name not in ('xray', 'singbox', 'both'):
        problems.append('rule.core.bad')
    if rule.network not in NETWORK_CHOICES:
        problems.append('rule.network.bad')
    if rule.protocol and rule.protocol not in PROTOCOL_CHOICES:
        problems.append('rule.protocol.bad')
    if rule.ports and not PORT_RE.match(rule.ports.strip()):
        problems.append('rule.ports.bad')
    for d in rule.domain_list:
        if not DOMAIN_RE.match(d):
            problems.append('rule.domain.bad')
            break
    for ip in rule.ip_list:
        if not IP_RE.match(ip):
            problems.append('rule.ip.bad')
            break
    # at least one match field, so a row cannot silently swallow everything
    if not (rule.domain_list or rule.ip_list or rule.ports.strip() or rule.protocol or rule.inbound_tag_list):
        problems.append('rule.match.required')
    # the protocol knob is an xray field, sing-box spells it differently
    if rule.protocol and rule.core_name in ('singbox',) and rule.protocol == 'bittorrent':
        problems.append('rule.protocol.xray_only')
    return problems


def ws_rule_for_xray(rule: RoutingRule) -> dict | None:
    """One rule in xray's spelling, or None when the row belongs to sing-box
    only. The target word: a custom outbound keeps its full tag, the builtins
    map to what the core ships."""
    if rule.core_name == 'singbox':
        return None
    out = {'type': 'field'}
    if rule.domain_list:
        out['domain'] = rule.domain_list
    if rule.ip_list:
        out['ip'] = rule.ip_list
    if rule.ports.strip():
        out['port'] = rule.ports.strip()
    if rule.protocol:
        out['protocol'] = [rule.protocol]
    if rule.network in ('tcp', 'udp'):
        out['network'] = rule.network
    if rule.inbound_tag_list:
        out['inboundTag'] = rule.inbound_tag_list
    if rule.target == 'block':
        out['outboundTag'] = 'blackhole'
    elif rule.target == 'direct':
        out['outboundTag'] = 'freedom'
    elif rule.target == 'proxy':
        out['outboundTag'] = 'proxy'
    elif rule.target == 'warp':
        out['outboundTag'] = 'WARP'
    else:
        out['outboundTag'] = rule.target  # a custom-out-* tag
    return out


def ws_rule_for_singbox(rule: RoutingRule) -> dict | None:
    """One rule in sing-box's spelling, or None when the row belongs to xray
    only. The same builtin mapping, block -> blackhole via the route action."""
    if rule.core_name == 'xray':
        return None
    out = {}
    if rule.domain_list:
        suffix = [d for d in rule.domain_list if not d.startswith(('geosite:', 'domain:', 'ext:'))]
        plain = [d.removeprefix('domain:').removeprefix('ext:').split(':')[-1]
                 for d in rule.domain_list if d.startswith(('domain:', 'ext:'))]
        geosite = [d.removeprefix('geosite:') for d in rule.domain_list if d.startswith('geosite:')]
        if suffix:
            out['domain_suffix'] = suffix
        if plain:
            out['domain'] = plain
        if geosite:
            out['rule_set'] = [f'geosite-{g}' for g in geosite]
    if rule.ip_list:
        geo = [ip.removeprefix('geoip:') for ip in rule.ip_list if ip.startswith('geoip:')]
        cidr = [ip for ip in rule.ip_list if not ip.startswith('geoip:')]
        if geo:
            out['rule_set'] = out.get('rule_set', []) + [f'geoip-{g}' for g in geo]
        if cidr:
            out['ip_cidr'] = cidr
    if rule.ports.strip():
        parts = []
        for chunk in rule.ports.strip().split(','):
            parts.extend([int(p) for p in chunk.split('-')] if '-' in chunk else [int(chunk)])
        out['port'] = parts if len(parts) > 1 else parts[0]
    if rule.protocol and rule.protocol != 'bittorrent':
        out['protocol'] = [rule.protocol]
    if rule.network in ('tcp', 'udp'):
        out['network'] = rule.network
    if rule.inbound_tag_list:
        out['inbound'] = rule.inbound_tag_list
    if rule.target == 'block':
        out['outbound'] = 'blackhole'
    elif rule.target == 'direct':
        out['outbound'] = 'freedom'
    elif rule.target == 'proxy':
        out['outbound'] = 'proxy'
    elif rule.target == 'warp':
        out['outbound'] = 'WARP'
    else:
        out['outbound'] = rule.target
    return out


def ws_rules_for_render(core: str) -> list:
    """The enabled rules of one core, in order, ready to drop into the routing
    template. core is 'xray' or 'singbox'."""
    try:
        rows = RoutingRule.all_rows()
    except Exception:
        return []
    return [r for r in rows if r.enable and r.core_name in (core, 'both')]


def ws_export_rows() -> list:
    return [r.to_dict() for r in RoutingRule.all_rows()]


def ws_rev(rows: list, core: str) -> int:
    """A cheap fingerprint the page's test button uses, same idea as the
    outbounds page: the render only needs to know whether anything changed."""
    import hashlib
    mine = [r for r in rows if r.get('core') in (core, 'both') and r.get('enable', True)]
    blob = repr(sorted((r['id'], r.get('order', 0), r.get('target', '')) for r in mine))
    return int(hashlib.sha1(blob.encode()).hexdigest()[:8], 16)
