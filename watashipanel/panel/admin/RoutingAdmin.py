# watashi v12.3.56dev: the Routing page (the advanced mode), phase 2 of the
# hand built outbounds.
"""The hand built routing rules, the Outbounds page's sibling. The rows live in
the routing_rule table and ride into both cores through the render (the
xray/configs/03_routing.json.j2 and singbox/configs/03_routing.json.j2
templates write them next to their own rules), so the page itself only builds,
validates and orders them — the apply of a rule is the apply of the whole core,
the same button every other page uses.

The test asks the commander to run the real binary on the candidate routing
section (xray run -test, sing-box check) before anything is saved, so a rule
that would keep the core from starting never reaches the server."""
import re

from flask import render_template, request, jsonify
from flask_babel import gettext as _
from flask_classful import FlaskView, route

from watashipanel.auth import login_required
from watashipanel.database import db
from watashipanel.models import Role, CustomOutbound, RoutingRule
from watashipanel.hutils.proxy import routing_rule as rr
from watashipanel.panel.run_commander import ws_custom_routes


class RoutingAdmin(FlaskView):
    default_methods = ['GET', 'POST']

    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def index(self):
        rows = [r.to_dict() for r in RoutingRule.all_rows()]
        targets = rr.ws_target_choices(CustomOutbound.all_rows())
        return render_template('routing.html', rows=rows, targets=targets,
                               cores=['both', 'xray', 'singbox'],
                               networks=list(rr.NETWORK_CHOICES),
                               protocols=list(rr.PROTOCOL_CHOICES))

    @route('/save', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def save(self):
        clean = request.get_json(silent=True) or {}
        keep_id = clean.get('id') or None
        row = RoutingRule.from_dict(clean, keep_id=bool(keep_id))
        if keep_id:
            current = db.session.get(RoutingRule, int(keep_id))
            if not current:
                return jsonify({'success': False, 'message': _('Rule not found')})
            row.id = current.id
            row.created_at = current.created_at
            db.session.delete(current)
        if not row.order:
            last = RoutingRule.all_rows()
            row.order = (last[-1].order + 10) if last else 10
        problems = rr.ws_validate(row)
        if problems:
            db.session.rollback()
            return jsonify({'success': False, 'message': _('; ').join(_(p) for p in problems)})
        db.session.add(row)
        db.session.commit()
        return jsonify({'success': True, 'row': row.to_dict()})

    @route('/delete', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def delete(self):
        clean = request.get_json(silent=True) or {}
        row = db.session.get(RoutingRule, int(clean.get('id') or 0))
        if not row:
            return jsonify({'success': False, 'message': _('Rule not found')})
        db.session.delete(row)
        db.session.commit()
        return jsonify({'success': True})

    @route('/toggle', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def toggle(self):
        clean = request.get_json(silent=True) or {}
        row = db.session.get(RoutingRule, int(clean.get('id') or 0))
        if not row:
            return jsonify({'success': False, 'message': _('Rule not found')})
        row.enable = not row.enable
        db.session.commit()
        return jsonify({'success': True, 'enable': row.enable})

    @route('/move', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def move(self):
        """One step up or down: the rows run in order, the page shows arrows."""
        clean = request.get_json(silent=True) or {}
        row = db.session.get(RoutingRule, int(clean.get('id') or 0))
        way = clean.get('way')
        if not row or way not in ('up', 'down'):
            return jsonify({'success': False, 'message': _('Rule not found')})
        rows = RoutingRule.all_rows()
        index = rows.index(row)
        other = rows[index - 1] if way == 'up' else (rows[index + 1] if index + 1 < len(rows) else None)
        if other:
            row.order, other.order = other.order, row.order
            db.session.commit()
        return jsonify({'success': True})

    @route('/test', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def test(self):
        """The whole section as it would ride into the core, checked by the
        real binary before the owner saves anything."""
        core = (request.get_json(silent=True) or {}).get('core', 'xray')
        rules = []
        for one in RoutingRule.all_rows():
            if not one.enable:
                continue
            built = rr.ws_rule_for_xray(one) if core == 'xray' else rr.ws_rule_for_singbox(one)
            if built:
                rules.append(built)
        ok, output = ws_custom_routes('test-singbox' if core == 'singbox' else 'test',
                                     json.dumps({'rules': rules}))
        return jsonify({'success': ok, 'message': (output or ('ok' if ok else _('The core refused the routing')))[:700]})
