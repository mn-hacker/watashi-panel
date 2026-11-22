# watashi v12.3.55dev: the outbounds the owner builds by hand in the advanced mode.
"""One row is one outbound, described the way x-ui and Marzban describe theirs:
a protocol, a server address, a port and a security layer. The fields that only
some combinations need (a path, a service name, the REALITY keys, the WireGuard
peers and so on) live in the settings column as JSON, so a new knob never needs
a new column and an older panel can still read the table.

An outbound sends nothing by itself: the traffic reaches it through the routing
rules (phase two), so the apply only tests that the whole config with these rows
still loads in the core. Unlike an inbound, two rows may share one port — the
remote servers are different machines — but a tag must be unique inside its
core, because the routing rules address the rows by tag.
"""
import datetime
import json

from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime

from watashipanel.database import db

# watashi v12.3.55dev: these protocols are served by sing-box, every other one
# by xray. The core follows from the protocol, so the table needs no new column
# and a backup of this release still restores on the one before it.
SINGBOX_OUTBOUND_PROTOCOLS = ('hysteria2', 'tuic', 'shadowtls', 'ss2022', 'wireguard')

# the reserved tags of the base configs; a custom row can never take them
RESERVED_TAGS = ('fragment', 'direct', 'block', 'dns-out', 'warp', 'warp-ir',
                 'custom-in', 'proxy', 'auto', 'block-direct')

XRAY_OUTBOUND_PROTOCOLS = ('vless', 'vmess', 'trojan', 'shadowsocks', 'socks', 'http', 'wireguard', 'freedom', 'blackhole', 'dns')
SINGBOX_PROTOCOLS_FULL = ('vless', 'vmess', 'trojan', 'shadowsocks', 'socks', 'http', 'hysteria2', 'tuic', 'shadowtls', 'ss2022', 'wireguard')


class CustomOutbound(db.Model):  # type: ignore
    __tablename__ = 'custom_outbound'
    id = Column(Integer, primary_key=True, autoincrement=True)
    child_id = Column(Integer, default=0, nullable=False)
    enable = Column(Boolean, default=True, nullable=False)
    remark = Column(String(128), default='', nullable=False)
    protocol = Column(String(16), default='vless', nullable=False)
    address = Column(String(255), default='', nullable=False)
    port = Column(Integer, default=443, nullable=False)
    tag_name = Column(String(64), default='', nullable=False)
    network = Column(String(16), default='tcp', nullable=False)
    security = Column(String(16), default='none', nullable=False)
    settings = Column(Text, default='{}')
    # watashi v12.3.57dev: the live probe bookkeeping (the Connect column of the
    # Outbounds page) and the fallback chain (an ordered list of tag_names to
    # try when this outbound fails its probe)
    probe_ip = Column(String(64), default='', nullable=False)
    probe_country = Column(String(8), default='', nullable=False)
    probe_latency = Column(Integer, default=0, nullable=False)
    probe_at = Column(DateTime, nullable=True)
    probe_ok = Column(Boolean, default=False, nullable=False)
    fallbacks = Column(Text, default='', nullable=False)  # one tag_name per line, in order
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    @property
    def tag(self) -> str:
        # the xray driver recognises its own outbounds by this prefix
        if self.tag_name:
            return f'custom-out-{self.tag_name}'
        return f'custom-out-{self.id}'

    @property
    def core(self) -> str:
        # which core serves this row, xray or singbox
        return 'singbox' if self.protocol in SINGBOX_OUTBOUND_PROTOCOLS else 'xray'

    @property
    def opts(self) -> dict:
        try:
            value = json.loads(self.settings or '{}')
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @opts.setter
    def opts(self, value: dict):
        self.settings = json.dumps(value or {}, ensure_ascii=False, sort_keys=True)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'enable': bool(self.enable),
            'remark': self.remark or '',
            'protocol': self.protocol,
            'address': self.address or '',
            'port': int(self.port or 0),
            'tag_name': self.tag_name or '',
            'network': self.network,
            'security': self.security,
            'settings': self.opts,
            'core': self.core,  # read only, from_dict ignores it
            # watashi v12.3.57dev: the probe report and the fallback chain
            'probe_ip': self.probe_ip or '',
            'probe_country': self.probe_country or '',
            'probe_latency': int(self.probe_latency or 0),
            'probe_at': self.probe_at.isoformat() if self.probe_at else '',
            'probe_ok': bool(self.probe_ok),
            'fallbacks': self.fallback_list,
        }

    @property
    def fallback_list(self) -> list:
        return [d.strip() for d in (self.fallbacks or '').splitlines() if d.strip()]

    @staticmethod
    def from_dict(row: dict, keep_id: bool = True) -> 'CustomOutbound':
        one = CustomOutbound()
        if keep_id and row.get('id'):
            one.id = int(row['id'])
        one.child_id = 0
        one.enable = bool(row.get('enable', True))
        one.remark = str(row.get('remark') or '')[:128]
        one.protocol = str(row.get('protocol') or 'vless')[:16]
        one.address = str(row.get('address') or '')[:255]
        one.port = int(row.get('port') or 443)
        one.tag_name = str(row.get('tag_name') or '')[:64]
        one.network = str(row.get('network') or 'tcp')[:16]
        one.security = str(row.get('security') or 'none')[:16]
        settings = row.get('settings')
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except Exception:
                settings = {}
        one.opts = settings if isinstance(settings, dict) else {}
        fb = row.get('fallbacks')
        if isinstance(fb, list):
            one.fallbacks = '\n'.join(str(v).strip() for v in fb if str(v).strip())
        elif isinstance(fb, str):
            one.fallbacks = fb
        # the probe fields are never taken from a dict: only the probe writes them
        return one

    @staticmethod
    def all_rows() -> list['CustomOutbound']:
        try:
            return CustomOutbound.query.filter(CustomOutbound.child_id == 0).order_by(CustomOutbound.id).all()
        except Exception:
            # the table is created by the migration of this release; until it
            # ran there simply are no hand built outbounds.
            db.session.rollback()
            return []

    @staticmethod
    def bulk_register(rows: list, commit: bool = True):
        """Backup restore: the section replaces what is there, row for row."""
        if not isinstance(rows, list):
            return
        CustomOutbound.query.delete()
        for row in rows:
            if not isinstance(row, dict) or not row.get('address'):
                continue
            try:
                db.session.add(CustomOutbound.from_dict(row))
            except (TypeError, ValueError) as problem:
                # one broken row of a hand edited file is skipped, not the restore
                print('a custom outbound of the backup was skipped', problem)
        if commit:
            db.session.commit()
