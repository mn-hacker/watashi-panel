# watashi v12.3.40dev: the old project name was taken out of this file (names, paths and links only)
"""Device (HWID) limit enforcement (ported from PasarGuard). Fully toggleable."""
import hashlib
import re
from functools import lru_cache

from flask import request, g

from watashipanel.models import hconfig, ConfigEnum
from watashipanel.models import hwid as hwid_model

H_HWID = 'x-hwid'
H_DEVICE_OS = 'x-device-os'
H_VER_OS = 'x-ver-os'
H_DEVICE_MODEL = 'x-device-model'
# watashi v12.3.50dev: the app that reports the id and the carrier of the
# device, both optional, for the monitoring page only
H_APP_NAME = 'x-app-name'
H_OPERATOR = 'x-operator'

R_ACTIVE = 'x-hwid-active'
R_LIMIT = 'x-hwid-limit'
R_NOT_SUPPORTED = 'x-hwid-not-supported'
R_MAX_REACHED = 'x-hwid-max-devices-reached'

MAX_HWID_LEN = 256

# watashi v12.3.53dev: apps that send no device id (v2rayNG and the like) still
# get a stable monitoring record from a stand-in id derived from the UA. It
# carries a space (real ids never do) so it can never collide with a real one.
DERIVED_HWID_PREFIX = 'ua '

UA_APP_PATTERNS = [
    ('v2rayNG', re.compile(r'v2rayNG/([\d.]+)', re.I)),
    ('Happ', re.compile(r'Happ/([\d.]+)', re.I)),
    ('v2rayN', re.compile(r'v2rayN/([\d.]+)', re.I)),
    ('Hiddify', re.compile(r'Hiddify(?:Next)?/([\d.]+)', re.I)),
    ('v2Box', re.compile(r'v2Box/([\d.]+)', re.I)),
    ('Streisand', re.compile(r'Streisand/([\d.]+)', re.I)),
    ('Shadowrocket', re.compile(r'Shadowrocket/([\d.]+)', re.I)),
    ('NekoBox', re.compile(r'NekoBox(?:2|ForAndroid)?/([\d.]+)', re.I)),
    ('Nekoray', re.compile(r'Nekoray/([\d.]+)', re.I)),
    ('Karing', re.compile(r'Karing/([\d.]+)', re.I)),
    ('Clash Meta for Android', re.compile(r'ClashMetaForAndroid/([\d.]+)', re.I)),
    ('Clash for Android', re.compile(r'Clash(?:For)?Android/([\d.]+)', re.I)),
    ('Clash Verge', re.compile(r'Clash-?Verge/([\d.]+)', re.I)),
    ('Stash', re.compile(r'Stash/([\d.]+)', re.I)),
    ('Loon', re.compile(r'Loon/([\d.]+)', re.I)),
    ('sing-box', re.compile(r'sing-box(?:\sSFA|\sSFI|\sSFM|\sSFT)?(?:/|\s)([\d.]+)', re.I)),
    ('sing-box (SFA)', re.compile(r'\bSFA/([\d.]+)', re.I)),
    ('sing-box (SFI)', re.compile(r'\bSFI/([\d.]+)', re.I)),
    ('sing-box (SFM)', re.compile(r'\bSFM/([\d.]+)', re.I)),
    ('sing-box (SFT)', re.compile(r'\bSFT/([\d.]+)', re.I)),
    ('FairVPN', re.compile(r'FairVPN/([\d.]+)', re.I)),
    ('Material Xray', re.compile(r'Material Xray/([\d.]+)', re.I)),
    ('mins', re.compile(r'mins/([\d.]+)', re.I)),
    ('INCY', re.compile(r'INCY/([\d.]+)', re.I)),
    ('v2RayTun', re.compile(r'v2RayTun/([\d.]+)', re.I)),
    ('Oblivion', re.compile(r'Oblivion(?:Warp)?/([\d.]+)', re.I)),
    ('Xray-core', re.compile(r'Xray-core/([\d.]+)', re.I)),
    ('curl', re.compile(r'curl/([\d.]+)', re.I)),
]

# watashi v12.3.55dev: apps that only exist on one platform and send no OS hint
# at all (v2rayNG and the like) still get a correct OS label on the monitoring
# page. Multi-platform apps (Hiddify, Karing, Oblivion, FairVPN, Nekoray) stay
# out of the map on purpose: guessing would be wrong half of the time.
APP_PLATFORM_FALLBACK = {
    'v2rayNG': 'Android',
    'v2RayTun': 'Android',
    'NekoBox': 'Android',
    'NekoBox2': 'Android',
    'Clash Meta for Android': 'Android',
    'Clash for Android': 'Android',
    'Material Xray': 'Android',
    'mins': 'Android',
    'INCY': 'Android',
    'v2rayN': 'Windows',
    'v2Box': 'iOS',
    'Streisand': 'iOS',
    'Shadowrocket': 'iOS',
    'Stash': 'iOS',
    'Loon': 'iOS',
    'sing-box (SFA)': 'Android',
    'sing-box (SFI)': 'iOS',
    'sing-box (SFM)': 'macOS',
    'sing-box (SFT)': 'Windows',
}


def platform_for_app(app_name: str) -> str:
    """The platform an app always runs on, or '' when it is multi-platform.

    Stored app names carry the version ("v2rayNG 2.3.15"), so both the bare
    name and the name with a trailing version match.
    """
    a = str(app_name or '').strip()
    if not a:
        return ''
    for name, platform in APP_PLATFORM_FALLBACK.items():
        if a == name or a.startswith(name + ' '):
            return platform
    return ''

# The carrier of the device, resolved from the ASN of the IP that fetched the
# subscription. Iranian mobile and data networks get a proper name; every
# other network in the world falls back on its ASN organisation (GeoLite2)
# plus the country code, so nothing shows as unknown just because the app
# cannot report a carrier.
ASN_OPERATORS = {
    '197207': 'Hamrah Aval (MCI)',
    '58224': 'MCI Mobile Broadband',
    '12880': 'TCI (Mokhaberat)',
    '44244': 'Irancell',
    '57218': 'Rightel',
    '61173': 'Samantel',
    '31549': 'Shatel',
    '205647': 'Shatel',
    '56402': 'Hiweb',
    '41689': 'Asiatech',
    '43754': 'Asiatech',
    '16322': 'ParsOnline',
    '50810': 'Mobinnet',
    '205207': 'Respina',
    '25184': 'Afranet',
    '394510': 'ZiTel',
    '206065': 'ZiTel',
    '49100': 'Pishgaman',
    '39308': 'ASK',
    '48011': 'Soroush Rayaneh',
    '60627': 'Pardis (Pars Dev Ab)',
    '42907': 'Fanava',
    '59796': 'Farhang Azma',
    '60932': 'Iran System',
    '42337': 'Aboreihan',
    '59588': 'Pars Dezh Gostar',
    '62200': 'Flex Pen',
    '204650': 'Rasan (Scarlet)',
    '44869': 'Sabanet',
    '59629': 'Ideh Pardazan (Dadeh Gostar)',
}


def app_from_user_agent(ua: str):
    """Return (app name, version) detected from the User-Agent, or ('', '')."""
    ua = str(ua or '')
    for name, pattern in UA_APP_PATTERNS:
        m = pattern.search(ua)
        if m:
            ver = m.group(1) or ''
            return name, ver[:16]
    return '', ''


def ua_device_os(ua: str) -> str:
    ua = str(ua or '')
    if re.search(r'android', ua, re.I):
        return 'Android'
    if re.search(r'iphone|ipad|ios', ua, re.I):
        return 'iOS'
    if re.search(r'windows', ua, re.I):
        return 'Windows'
    if re.search(r'mac os|macintosh', ua, re.I):
        return 'macOS'
    if re.search(r'linux', ua, re.I):
        return 'Linux'
    return ''


def ua_device_model(ua: str) -> str:
    """v2rayNG and Happ put the handset model inside the UA parentheses, e.g.
    'v2rayNG/1.9.24 (sm-s918b Android 14)' — lift it out when no header has it."""
    m = re.search(r'\(([^;()]+?)[,;]?\s*(?:Android|iOS|iPhone OS|HarmonyOS)', str(ua or ''), re.I)
    if m:
        model = m.group(1).strip().strip(',')
        if model and not re.fullmatch(r'(?i)(Linux|U|W)', model):
            return model[:32]
    return ''


def ua_os_version(ua: str) -> str:
    ua = str(ua or '')
    m = re.search(r'Android[,\s/]+(\d+(?:\.\d+)*)', ua, re.I)
    if m:
        return m.group(1)
    m = re.search(r'iOS[,\s/]+(\d+(?:\.\d+)*)', ua, re.I)
    if m:
        return m.group(1)
    m = re.search(r'(?:iPhone OS|Mac OS X)[,\s/]?(\d+(?:[_.]\d+)*)', ua, re.I)
    return m.group(1).replace('_', '.') if m else ''


def derived_hwid(user_id: int, ua: str) -> str:
    """A stable stand-in id for apps that send no real one: hash(user, UA)."""
    digest = hashlib.sha256(f'{int(user_id)}|{ua}'.encode('utf-8', 'ignore')).hexdigest()[:24]
    return f'{DERIVED_HWID_PREFIX}{digest}'


def operator_from_ip() -> str:
    """The carrier behind the subscriber's IP. Known Iranian networks get a
    proper name; every other operator in the world falls back on its ASN
    organisation (GeoLite2) plus the country code, so nothing is 'unknown'
    only because the app cannot report a carrier."""
    try:
        from watashipanel.hutils.network import auto_ip_selector as aips
        ip = aips.get_real_user_ip()
        if not ip:
            return ''
        asn_id = ''
        try:
            asn_id = str(aips.get_asn_id(ip) or '')
        except Exception:
            asn_id = ''
        if asn_id and asn_id != 'unknown':
            name = ASN_OPERATORS.get(asn_id)
            if name:
                return name
            short = aips.get_asn_short_name(ip)
            if short and short != 'unknown':
                return short
        org = ''
        country = ''
        try:
            res = aips.IPASN.get(ip) if hasattr(aips.IPASN, 'get') else None
            if res:
                org = str(res.get('autonomous_system_organization', '') or '').strip()
        except Exception:
            org = ''
        try:
            country = aips.get_country(ip) or ''
        except Exception:
            country = ''
        foreign = bool(country) and country != 'unknown'
        if org:
            return f'{org[:20]} ({country})' if foreign else org[:24]
        if asn_id and asn_id != 'unknown':
            return f'ASN {asn_id} ({country})' if foreign else f'ASN {asn_id}'
        if foreign:
            return f'Other ({country})'
        # No geo database installed: never show a broken 'ASN unknown' label.
        return ''
    except Exception:
        return ''


def is_enabled():
    return bool(hconfig(ConfigEnum.hwid_limit_enable))


def is_forced():
    return bool(hconfig(ConfigEnum.hwid_forced))


def is_local_request():
    try:
        if request.remote_addr != '127.0.0.1':
            return False
        if 'X-Forwarded-For' in request.headers:
            return False
        return True
    except Exception:
        return False


def is_enabled_for_user(user):
    if is_local_request():
        return False
    if user is None:
        return is_enabled()
    # watashi v12.2.71: the positive switch wins. It can only turn the limit
    # ON for one user, never silently off, so a fresh False changes nothing
    # for anybody who is already running.
    if getattr(user, 'hwid_enforce', False):
        return True
    if getattr(user, 'hwid_disabled', False):
        return False
    return is_enabled()


def get_effective_limit(user):
    per_user = getattr(user, 'hwid_limit', 0) or 0
    if per_user and per_user > 0:
        return int(per_user)
    try:
        return int(hconfig(ConfigEnum.hwid_limit_default) or 0)
    except (TypeError, ValueError):
        return 0


def read_headers():
    h = request.headers
    ua = (h.get('User-Agent') or '').strip()

    # Try multiple common HWID headers
    hwid = h.get(H_HWID)
    if not hwid:
        hwid = h.get('v2box-device-id') or h.get('x-device-id') or h.get('device-id') or h.get('hardware-id') or request.args.get('hwid') or request.args.get('device_id') or ''

    # watashi v12.3.53dev: apps that never send the custom headers (v2rayNG and
    # most others) are still recognised from their User-Agent, so the
    # monitoring page can name the app, the OS and the carrier.
    app_name = (h.get(H_APP_NAME) or '').strip()
    bare_app = app_name
    if not app_name:
        bare_app, app_ver = app_from_user_agent(ua)
        if bare_app and app_ver:
            app_name = f'{bare_app} {app_ver}'

    device_os = (h.get(H_DEVICE_OS) or '').strip() or ua_device_os(ua)
    # watashi v12.3.55dev: v2rayNG and other single-platform apps never say
    # which OS they run on; the app name alone settles it.
    if not device_os:
        device_os = platform_for_app(bare_app)
    ver_os = (h.get(H_VER_OS) or '').strip() or ua_os_version(ua)
    device_model = (h.get(H_DEVICE_MODEL) or '').strip() or ua_device_model(ua)
    operator = (h.get(H_OPERATOR) or '').strip() or operator_from_ip()

    return dict(
        hwid=hwid.strip()[:MAX_HWID_LEN],
        device_os=device_os,
        ver_os=ver_os,
        device_model=device_model,
        app_name=app_name,
        operator=operator,
        ua=ua,
    )


def enforce(user):
    g.hwid_active = False
    g.hwid_limit = 0
    g.hwid_not_supported = False
    g.hwid_max_reached = False
    info = read_headers()
    hwid = info['hwid']
    ua = info.get('ua', '')
    # watashi v12.3.53dev: apps that send no device id at all (v2rayNG and the
    # like) still get a stable stand-in id derived from (user, UA), so the
    # monitoring page shows their device, app and carrier. The space in the
    # derived id keeps it from ever colliding with a real one.
    monitoring_only = False
    if not hwid and ua:
        hwid = derived_hwid(getattr(user, 'id', 0) or 0, ua) if user is not None and user.id else ''
        monitoring_only = bool(hwid)
    # watashi v12.3.50dev: remember the device of every user that reports one,
    # flagged or not, so the monitoring page can show the OS, the app and the
    # carrier for all of them. The bookkeeping never blocks anybody; the rest
    # of the function only runs when a limit really applies.
    if user is not None and user.id and hwid and not is_local_request():
        try:
            hwid_model.upsert_user_hwid(user.id, hwid, device_os=info['device_os'],
                                        ver_os=info['ver_os'], device_model=info['device_model'],
                                        app_name=info['app_name'], operator=info['operator'])
        except Exception:
            pass
    if monitoring_only:
        return True
    if user is None or not is_enabled_for_user(user):
        return True
    g.hwid_active = True
    limit = get_effective_limit(user)
    g.hwid_limit = limit
    if not hwid:
        # watashi v12.3.49dev: the forced switch only hardens the users the admin
        # flagged with "Enforce device limit"; the global limit still counts the
        # devices of everybody else, but never locks out apps without a HWID.
        if is_forced() and getattr(user, 'hwid_enforce', False):
            g.hwid_not_supported = True
            return False
        return True
    existing = hwid_model.get_user_hwid_by_value(user.id, hwid)
    if existing is not None:
        return True
    if limit and limit > 0:
        if hwid_model.get_user_hwid_count(user.id) >= limit:
            g.hwid_max_reached = True
            return False
    return True


def apply_response_headers(resp):
    try:
        if getattr(g, 'hwid_active', False):
            resp.headers[R_ACTIVE] = '1'
            resp.headers[R_LIMIT] = str(getattr(g, 'hwid_limit', 0))
        if getattr(g, 'hwid_not_supported', False):
            resp.headers[R_NOT_SUPPORTED] = '1'
        if getattr(g, 'hwid_max_reached', False):
            resp.headers[R_MAX_REACHED] = '1'
    except Exception:
        pass
    return resp
