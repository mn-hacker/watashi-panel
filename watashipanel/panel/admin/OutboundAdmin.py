# watashi v12.3.55dev: the Outbounds page (the advanced mode).
"""The hand built outbounds, the Inbounds page's sibling. The rows live in the
custom_outbound table and ride into the core config through the render (they
are part of `outbounds` of the file the panel writes), so the page itself only
builds, validates and tests them — the apply of an outbound is the apply of
the whole core, the same button every other page uses.

The test asks the commander to run the real binary on the candidate (xray run
-test, sing-box check) before anything is saved, so a row that would keep the
core from starting never reaches the server.
"""
import json
import re
import subprocess

from flask import render_template, request, jsonify
from flask import current_app as app
from flask_babel import gettext as _
from flask_classful import FlaskView, route

from watashipanel.auth import login_required
from watashipanel.database import db
from watashipanel.models import Role, CustomOutbound
from watashipanel.hutils.proxy import custom_outbound as wc
from watashipanel.panel.run_commander import ws_custom_outbounds, ws_test_connect
import datetime

WS_NOISE = re.compile(r'^(E|I|W)\d{4} \d{2}:\d{2}:\d{2} |\[.{0,10}\]\s+')
WS_ANSI = re.compile(r'\x1b\[[0-9;]*m')
WS_SB_LEVEL = re.compile(r'^\s*(DEBUG|INFO|WARN|ERROR|FATAL)\s+')


def ws_core_says(output: str, core: str) -> str:
    """The line of a test that explains a refusal, without the noise."""
    lines = [WS_NOISE.sub('', WS_ANSI.sub('', line).strip()) for line in str(output or '').splitlines()]
    lines = [line for line in lines if line]
    for line in reversed(lines):
        if line.startswith('AssertionError: '):
            return line[len('AssertionError: '):].removeprefix('Error: ')[:700]
    for line in reversed(lines):
        if core == 'singbox' and (line.startswith('FATAL') or line.startswith('ERROR')):
            text = re.sub(r'^decode config at [^:]*: ', '', line[level_end(line):]) if False else line
            return text.strip()[:700]
        if core == 'xray' and ('failed to load config' in line or line.startswith('Failed to start')):
            return (line.rsplit('] > ', 1)[-1] if '] > ' in line else line).replace('infra/conf: ', '').strip()[:700]
    return ' '.join(lines[-3:])[:700]


def level_end(line: str) -> int:
    m = WS_SB_LEVEL.match(line)
    return m.end() if m else 0


def ws_refusal(row, output: str) -> str:
    if row.core == 'singbox':
        return _('sing-box does not accept this outbound:') + ' ' + ws_core_says(output, 'singbox')
    return _('xray does not accept this outbound:') + ' ' + ws_core_says(output, 'xray')


class OutboundAdmin(FlaskView):
    default_methods = ['GET', 'POST']

    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def index(self):
        rows = [r.to_dict() for r in CustomOutbound.all_rows()]
        return render_template('outbounds.html',
                               rows=rows,
                               xray_protocols=wc.XRAY_OUTBOUND_PROTOCOLS,
                               singbox_protocols=wc.SINGBOX_PROTOCOLS_FULL,
                               reserved_tags=wc.RESERVED_TAGS)

    @route('/save', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def save(self):
        clean = request.get_json(silent=True) or {}
        keep_id = clean.get('id') or None
        row = CustomOutbound.from_dict(clean, keep_id=bool(keep_id))
        if keep_id:
            current = db.session.get(CustomOutbound, int(keep_id))
            if not current:
                return jsonify({'success': False, 'message': _('Outbound not found')})
            row.id = current.id
            row.created_at = current.created_at
            db.session.delete(current)
        if not row.tag_name:
            row.tag_name = wc.ws_suggest_tag(row)
        problems = wc.ws_validate(row)
        if problems:
            db.session.rollback()
            return jsonify({'success': False, 'message': '; '.join(str(_(p)) for p in problems)})
        db.session.add(row)
        db.session.commit()
        return jsonify({'success': True, 'row': row.to_dict()})

    @route('/delete', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def delete(self):
        clean = request.get_json(silent=True) or {}
        row = db.session.get(CustomOutbound, int(clean.get('id') or 0))
        if not row:
            return jsonify({'success': False, 'message': _('Outbound not found')})
        db.session.delete(row)
        db.session.commit()
        return jsonify({'success': True})

    @route('/toggle', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def toggle(self):
        clean = request.get_json(silent=True) or {}
        row = db.session.get(CustomOutbound, int(clean.get('id') or 0))
        if not row:
            return jsonify({'success': False, 'message': _('Outbound not found')})
        row.enable = not row.enable
        db.session.commit()
        return jsonify({'success': True, 'enable': bool(row.enable)})

    @route('/test', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def test(self):
        """The candidate through the real binary, without saving anything."""
        clean = request.get_json(silent=True) or {}
        keep_id = clean.get('id') or None
        row = CustomOutbound.from_dict(clean, keep_id=bool(keep_id))
        problems = wc.ws_validate(row)
        if problems:
            return jsonify({'success': False, 'message': '; '.join(str(_(p)) for p in problems)})
        candidate = wc.to_xray(row) if row.core == 'xray' else wc.to_singbox(row)
        file = wc.ws_test_file([candidate], core=row.core)
        ok, output = ws_custom_outbounds(
            'test-singbox' if row.core == 'singbox' else 'test',
            payload=json.dumps(file), timeout=90)
        if not ok:
            return jsonify({'success': False, 'message': ws_refusal(row, output)})
        return jsonify({'success': True, 'message': _('The core accepts this outbound')})

    @route('/rows', methods=['GET'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def rows(self):
        return jsonify({'success': True, 'rows': [r.to_dict() for r in CustomOutbound.all_rows()]})

    @route('/suggest-tag', methods=['GET'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def suggest_tag(self):
        clean = request.get_json(silent=True) or {}
        row = CustomOutbound()
        row.remark = clean.get('remark', '')
        row.address = clean.get('address', '')
        return jsonify({'success': True, 'tag': wc.ws_suggest_tag(row)})

    @route('/connect', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def connect(self):
        """watashi v12.3.57dev: the live probe of one row — the real core runs
        with only this outbound and the answer is the exit IP and country it
        truly used. The report is kept on the row (the Connect column)."""
        clean = request.get_json(silent=True) or {}
        row = db.session.get(CustomOutbound, int(clean.get('id') or 0))
        if not row:
            return jsonify({'success': False, 'message': _('Outbound not found')})
        built = wc.to_xray(row) if row.core == 'xray' else wc.to_singbox(row)
        if not built:
            return jsonify({'success': False, 'message': _('The core refused this outbound')})
        report = ws_test_connect(row.core, built)
        row.probe_ip = report.get('exit_ip') or ''
        row.probe_country = report.get('country') or ''
        row.probe_latency = int(report.get('latency_ms') or 0)
        row.probe_ok = bool(report.get('ok'))
        row.probe_at = datetime.datetime.now()
        db.session.commit()
        answer = {'success': report.get('ok'), 'exit_ip': row.probe_ip, 'country': row.probe_country,
                  'latency_ms': row.probe_latency}
        if not report.get('ok'):
            answer['message'] = (report.get('log') or _('The outbound did not answer'))[:500]
        return jsonify(answer)

    @route('/fallbacks', methods=['POST'])
    @login_required(roles={Role.super_admin, Role.admin, Role.custom})
    def fallbacks(self):
        """watashi v12.3.57dev: the ordered try-next list of one row. The names
        are tag_names of other custom outbounds; the probe of the row walks the
        list when its own tunnel fails and the page shows the chain."""
        clean = request.get_json(silent=True) or {}
        row = db.session.get(CustomOutbound, int(clean.get('id') or 0))
        if not row:
            return jsonify({'success': False, 'message': _('Outbound not found')})
        wanted = [str(v).strip() for v in (clean.get('fallbacks') or []) if str(v).strip()]
        known = {r.tag_name for r in CustomOutbound.all_rows() if r.id != row.id}
        wrong = [t for t in wanted if t not in known]
        if wrong:
            return jsonify({'success': False, 'message': _('Unknown outbound tags: ') + ', '.join(wrong)})
        row.fallbacks = '\n'.join(wanted)
        db.session.commit()
        return jsonify({'success': True, 'fallbacks': row.fallback_list})

