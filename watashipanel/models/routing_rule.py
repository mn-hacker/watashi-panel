# watashi v12.3.56dev: the routing rules the owner builds by hand in the
# advanced mode (the Routing page), phase 2 of the hand built outbounds.
"""One row is one routing rule, evaluated top down, exactly like a rule the
admin would write into xray/configs/03_routing.json.j2 by hand. Both cores read
the same rows: the xray form is written by xray/configs/03_routing.json.j2 and
the sing-box form by singbox/configs/03_routing.json.j2, each from its half of
the table (a rule can target one core or both).

The match fields follow the cores' own vocabulary: domains (list), ips (list,
CIDR or geoip:), ports (a string like 443 or 8000-9000), protocol (xray only:
http/tls/bittorrent), inbound tag and network (tcp/udp). The action is an
outbound tag: one of the built ins (direct/proxy/block/warp + the custom
outbound tags) or any tag the outbounds page defined.
"""
import datetime
import json

from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime

from watashipanel.database import db

# The actions that never belong to a custom outbound row: the cores ship them.
BUILTIN_TARGETS = ('direct', 'proxy', 'block', 'warp')

NETWORK_CHOICES = ('both', 'tcp', 'udp')
PROTOCOL_CHOICES = ('', 'http', 'tls', 'bittorrent')


class RoutingRule(db.Model):  # type: ignore
    __tablename__ = 'routing_rule'
    id = Column(Integer, primary_key=True, autoincrement=True)
    child_id = Column(Integer, default=0, nullable=False)
    enable = Column(Boolean, default=True, nullable=False)
    remark = Column(String(128), default='', nullable=False)
    # the order the rules run in; the page sorts by it and the render does too
    order = Column(Integer, default=0, nullable=False)
    # which core the rule is written into: xray, singbox or both
    core = Column(String(16), default='both', nullable=False)
    # match fields, all optional: an empty match means the rule catches the
    # traffic of the chosen inbound tags only, or everything when they are
    # empty too (that would be a final rule; the render refuses one that would
    # shadow every later row)
    domains = Column(Text, default='', nullable=False)   # one per line
    ips = Column(Text, default='', nullable=False)       # one per line, CIDR or geoip:
    ports = Column(String(64), default='', nullable=False)
    protocol = Column(String(16), default='', nullable=False)
    network = Column(String(16), default='both', nullable=False)
    inbound_tags = Column(Text, default='', nullable=False)  # one per line
    # where matched traffic goes: a BUILTIN_TARGET or a custom outbound tag
    target = Column(String(64), default='direct', nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)

    @property
    def core_name(self) -> str:
        return self.core if self.core in ('xray', 'singbox') else 'both'

    @property
    def domain_list(self) -> list:
        return [d.strip() for d in (self.domains or '').splitlines() if d.strip()]

    @property
    def ip_list(self) -> list:
        return [d.strip() for d in (self.ips or '').splitlines() if d.strip()]

    @property
    def inbound_tag_list(self) -> list:
        return [d.strip() for d in (self.inbound_tags or '').splitlines() if d.strip()]

    def to_dict(self) -> dict:
        return {'id': self.id,
                'enable': self.enable,
                'remark': self.remark or '',
                'order': self.order,
                'core': self.core_name,
                'domains': self.domain_list,
                'ips': self.ip_list,
                'ports': self.ports or '',
                'protocol': self.protocol or '',
                'network': self.network or 'both',
                'inbound_tags': self.inbound_tag_list,
                'target': self.target or 'direct'}

    @staticmethod
    def from_dict(row: dict, keep_id: bool = True) -> 'RoutingRule':
        one = RoutingRule()
        if keep_id and row.get('id'):
            one.id = int(row['id'])
        one.enable = bool(row.get('enable', True))
        one.remark = str(row.get('remark') or '')[:128]
        one.order = int(row.get('order') or 0)
        one.core = str(row.get('core') or 'both')[:16]

        def _lines(value) -> str:
            """A list becomes one entry per line; a string with newlines stays
            as it is (from_dict receives both shapes: the page sends lists,
            the backup sends lists, the validator reads lines)."""
            if isinstance(value, str):
                return value
            if isinstance(value, (list, tuple)):
                return '\n'.join(str(v).strip() for v in value if str(v).strip())
            return str(value or '')

        one.domains = _lines(row.get('domains'))
        one.ips = _lines(row.get('ips'))
        one.ports = str(row.get('ports') or '')[:64]
        one.protocol = str(row.get('protocol') or '')[:16]
        one.network = str(row.get('network') or 'both')[:16]
        one.inbound_tags = _lines(row.get('inbound_tags'))
        one.target = str(row.get('target') or 'direct')[:64]
        return one

    @staticmethod
    def all_rows() -> list:
        return RoutingRule.query.order_by(RoutingRule.order.asc(), RoutingRule.id.asc()).all()

    @staticmethod
    def bulk_register(rows: list):
        """The backup path: one create per row, ids kept, the table truncated
        first by the caller (the same contract custom_inbound uses)."""
        for data in rows:
            db.session.add(RoutingRule.from_dict(data, keep_id=True))
        db.session.commit()
