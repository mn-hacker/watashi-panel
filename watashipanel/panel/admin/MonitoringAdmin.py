# watashi v12.3.40dev: the old project name was taken out of this file (names, paths and links only)
"""
Device (HWID) Monitoring Admin Page

Shows each user's registered devices (HWID / model / OS / last-seen), their
device-limit status, and lets an admin remove a single device or reset all of
a user's devices. Replaces the old IP-based connection monitoring, which was
unreliable under Iran's CG-NAT.
"""

import datetime

from flask import render_template, request, jsonify
from flask_classful import FlaskView, route
from flask_babel import gettext as _
from loguru import logger

from watashipanel.auth import login_required
# Watashi v12.2.34b: hconfig and ConfigEnum belonged to the access-log
# reader, so they are not imported any more.
from watashipanel.models import User, Role
from watashipanel.models import get_user_hwids, get_user_hwid_count, delete_user_hwid, reset_user_hwids
from watashipanel.panel import hwid_limit

# watashi v12.3.55dev: the same online window as the users page
# (WS_ONLINE_WINDOW in AccountAdmin.py). The pages drive it from
# user.last_online, which the usage sync updates from the live xray counters.
ONLINE_WINDOW = 120  # seconds


def _clean_unknown(value):
    """Drop empty values and the 'unknown device' junk some old releases stored."""
    v = str(value or '').strip()
    if not v:
        return ''
    low = v.lower()
    if 'unknown' in low or 'ناشناس' in v or 'شناخته' in v:
        return ''
    return v


class MonitoringAdmin(FlaskView):
    """Admin view for monitoring user devices (HWID)."""

    decorators = [login_required({Role.super_admin, Role.admin, Role.custom})]

    def index(self):
        """Main device-monitoring page."""
        users = get_all_device_data()
        stats = _build_stats(users)
        os_distribution, device_trend, trend_spike = _build_device_analytics()
        app_distribution, operator_distribution = _build_more_analytics()
        return render_template('monitoring.html', users=users, stats=stats,
                               os_distribution=os_distribution, device_trend=device_trend,
                               trend_spike=trend_spike,
                               app_distribution=app_distribution,
                               operator_distribution=operator_distribution)

    @route('/api/devices', methods=['GET'])
    def api_devices(self):
        """JSON endpoint for AJAX refresh of the device table."""
        users = get_all_device_data()
        stats = _build_stats(users)
        return jsonify({'users': users, 'stats': stats})

    @route('/api/online-status', methods=['GET'])
    def api_online_status(self):
        """Lightweight heartbeat for the live online badges.

        watashi v12.3.55dev: the users page and the monitoring page read
        user.last_online from this endpoint every 30s, so a badge flips to
        online/offline without a full page reload.
        """
        try:
            uuids = request.args.get('uuids', '').split(',')
            uuids = [u for u in (u.strip() for u in uuids) if u][:500]
            if not uuids:
                return jsonify({'success': True, 'users': {}})
            now = datetime.datetime.now()
            rows = User.query.filter(User.uuid.in_(uuids)).all()
            out = {}
            for u in rows:
                stamp = getattr(u, 'last_online', None)
                try:
                    seen = max(0.0, (now - stamp).total_seconds()) if stamp is not None and stamp.year > 1 else None
                except (TypeError, ValueError):
                    seen = None
                out[u.uuid] = {
                    'online': bool(seen is not None and seen <= ONLINE_WINDOW),
                    'last_online': stamp.strftime('%Y-%m-%d %H:%M') if seen is not None else '',
                    'seconds': int(seen) if seen is not None else None,
                }
            return jsonify({'success': True, 'users': out})
        except Exception as e:
            logger.error(f"Error loading online status: {e}")
            return jsonify({'success': False, 'users': {}})

    @route('/device/<uuid>/remove', methods=['POST'])
    def remove_device(self, uuid):
        """Remove a single device (by HWID) from a user."""
        user = User.query.filter(User.uuid == uuid).first()
        if not user:
            return jsonify({'success': False, 'message': _('User not found')})
        hwid = (request.form.get('hwid') or request.values.get('hwid') or '').strip()
        if not hwid:
            return jsonify({'success': False, 'message': _('Device not found')})
        try:
            ok = delete_user_hwid(user.id, hwid)
            if ok:
                return jsonify({'success': True, 'message': _('Device removed successfully')})
            return jsonify({'success': False, 'message': _('Device not found')})
        except Exception as e:
            logger.error(f"Error removing device for {uuid}: {e}")
            return jsonify({'success': False, 'message': str(e)})

    @route('/user/<uuid>/reset-devices', methods=['POST'])
    def reset_devices(self, uuid):
        """Remove all devices for a user."""
        user = User.query.filter(User.uuid == uuid).first()
        if not user:
            return jsonify({'success': False, 'message': _('User not found')})
        try:
            count = reset_user_hwids(user.id)
            return jsonify({'success': True, 'message': _('Removed %(count)s device(s)', count=count)})
        except Exception as e:
            logger.error(f"Error resetting devices for {uuid}: {e}")
            return jsonify({'success': False, 'message': str(e)})

    @route('/devices/reset-over-limit', methods=['POST'])
    def reset_over_limit(self):
        """Reset (remove all) devices for every user currently over their limit."""
        try:
            users = get_all_device_data()
        except Exception as e:
            logger.error(f"Error listing users for bulk reset: {e}")
            return jsonify({'success': False, 'message': str(e)})
        over = [u for u in users if u.get('over_limit')]
        affected = 0
        removed = 0
        for u in over:
            try:
                user = User.query.filter(User.uuid == u['uuid']).first()
                if not user:
                    continue
                cnt = reset_user_hwids(user.id)
                removed += int(cnt or 0)
                affected += 1
            except Exception as e:
                logger.error(f"Bulk reset error for {u.get('uuid')}: {e}")
        if affected == 0:
            return jsonify({'success': True, 'affected': 0, 'removed': 0, 'message': _('No users are over their device limit')})
        return jsonify({'success': True, 'affected': affected, 'removed': removed, 'message': _('Reset %(a)s user(s) and removed %(d)s device(s)', a=affected, d=removed)})

    # Watashi v12.2.34: the standalone per-user log page is gone. It was the
    # last page still wearing the old theme, nothing in the panel linked to
    # it, and the feed it showed is served by the api route below, which the
    # monitoring page opens in a themed dialog.
    @route('/api/user/<uuid>/logs', methods=['GET'])
    def api_user_logs(self, uuid):
        """API endpoint for user logs (for AJAX refresh)."""
        user = User.query.filter(User.uuid == uuid).first()
        if not user:
            return jsonify({'error': _('User not found'), 'logs': []})

        logs = get_user_activity_logs(uuid, user.name)
        return jsonify({'logs': logs, 'user': {'name': user.name, 'uuid': uuid}})


def get_all_device_data():
    """Build the per-user device list shown on the monitoring page.

    Only users that have at least one registered device are included.
    Optimized to prevent N+1 query timeouts.
    """
    users = []
    
    from collections import defaultdict
    devices_by_user = defaultdict(list)
    try:
        from watashipanel.models.hwid import UserHWID
        # Fetch all devices at once
        all_devices = UserHWID.query.order_by(UserHWID.last_seen.desc()).all()
        for d in all_devices:
            devices_by_user[d.user_id].append(d)
    except Exception as e:
        logger.error(f"Error loading devices for monitoring: {e}")
        return []

    if not devices_by_user:
        return []

    try:
        # Fetch only the users that have devices
        user_ids_with_devices = list(devices_by_user.keys())
        # To avoid massive IN clauses, fetch all users or chunk them, but typically active users < 100k
        active_users = User.query.filter(User.id.in_(user_ids_with_devices)).all()
    except Exception as e:
        logger.error(f"Error loading users for device monitoring: {e}")
        active_users = []

    for user in active_users:
        # watashi v12.3.50dev: every user that reported a device shows up, not
        # only the flagged ones; the flag itself becomes a filter chip.
        devices = devices_by_user.get(user.id, [])
        if not devices:
            continue

        limit = hwid_limit.get_effective_limit(user)
        device_count = len(devices)
        over_limit = bool(limit and limit > 0 and device_count > limit)

        now = datetime.datetime.now()
        device_list = []
        for d in devices:
            os_name = _clean_unknown(d.device_os)
            ver_os = _clean_unknown(d.ver_os)
            model = _clean_unknown(d.device_model)
            os_label = f"{os_name} {ver_os}".strip()
            if not os_label:
                # watashi v12.3.55dev: v2rayNG and other single-platform apps
                # never report an OS; the app name settles it, even for the
                # rows recorded before the fallback existed.
                os_label = hwid_limit.platform_for_app(getattr(d, 'app_name', ''))
            try:
                seen_sec = max(0.0, (now - d.last_seen).total_seconds()) if d.last_seen else None
            except (TypeError, ValueError):
                seen_sec = None
            device_list.append({
                'hwid': d.hwid,
                # watashi v12.3.55dev: an OS-only fallback must also fill the
                # model slot, otherwise the UI shows "Android" as name and
                # "دستگاه ناشناس" as model for the same row.
                'model': model or os_label or _('Unknown device'),
                'os': os_label or model or _('Unknown device'),
                'name': ' '.join(p for p in (os_label, model) if p) or os_label or model or _('Unknown device'),
                'online': bool(seen_sec is not None and seen_sec <= ONLINE_WINDOW),
                'last_seen': d.last_seen.strftime('%Y-%m-%d %H:%M') if d.last_seen else '',
                'created_at': d.created_at.strftime('%Y-%m-%d %H:%M') if d.created_at else '',
                'app': getattr(d, 'app_name', '') or '',
                'operator': getattr(d, 'operator', '') or '',
            })

        stamp = getattr(user, 'last_online', None)
        try:
            online_sec = max(0.0, (now - stamp).total_seconds()) if stamp is not None and stamp.year > 1 else None
        except (TypeError, ValueError):
            online_sec = None
        users.append({
            'uuid': user.uuid,
            'name': user.name,
            'is_active': bool(getattr(user, 'is_active', True)),
            'enabled': True,
            'limit': limit,
            'device_count': device_count,
            'over_limit': over_limit,
            'enforced': bool(getattr(user, 'hwid_enforce', False)),
            # watashi v12.3.55dev: the real total usage, the same field the
            # users page reads, so the modal never shows stale decoration.
            'total_usage': int(getattr(user, 'current_usage', 0) or 0),
            # watashi v12.3.55dev: the same online status the users page shows
            # (user.last_online within the same 120s window), not "any device
            # was ever seen".
            'online': bool(online_sec is not None and online_sec <= ONLINE_WINDOW),
            'last_online': stamp.strftime('%Y-%m-%d %H:%M') if online_sec is not None else '',
            'devices': device_list,
        })

    # Show users over their limit first, then by device count (desc).
    users.sort(key=lambda u: (not u['over_limit'], -u['device_count']))
    return users


def _build_stats(users):
    """Summary counters for the stat cards."""
    return {
        'users_with_devices': len(users),
        'total_devices': sum(u['device_count'] for u in users),
        'total_users': User.query.count(),
        'users_over_limit': sum(1 for u in users if u['over_limit']),
        'limit_enabled': hwid_limit.is_enabled(),
        'forced': hwid_limit.is_forced(),
    }


def _os_bucket(raw):
    r = str(raw or '').strip().lower()
    if not r:
        return 'Unknown'
    if 'android' in r:
        return 'Android'
    if 'ios' in r or 'iphone' in r or 'ipad' in r:
        return 'iOS'
    if 'mac' in r or 'darwin' in r:
        return 'macOS'
    if 'win' in r:
        return 'Windows'
    if 'linux' in r:
        return 'Linux'
    return str(raw).strip()[:16].title()


def _build_device_analytics():
    """OS distribution, 30-day new-device trend, and a recent-spike alert."""
    import datetime as _dt
    import statistics as _st
    os_counts = {}
    trend_map = {}
    today = _dt.date.today()
    start = today - _dt.timedelta(days=29)
    try:
        from watashipanel.models.hwid import UserHWID
        all_devices = UserHWID.query.all()
    except Exception as e:
        logger.error(f"Error loading devices for analytics: {e}")
        all_devices = []

    for d in all_devices:
        bucket = _os_bucket(getattr(d, 'device_os', '') or '')
        os_counts[bucket] = os_counts.get(bucket, 0) + 1
        c = getattr(d, 'created_at', None)
        if c is not None:
            try:
                cd = c.date()
            except Exception:
                cd = None
            if cd is not None and start <= cd <= today:
                trend_map[cd] = trend_map.get(cd, 0) + 1

    os_distribution = [{'label': k, 'count': v} for k, v in sorted(os_counts.items(), key=lambda kv: -kv[1])]

    device_trend = []
    for i in range(30):
        dd = start + _dt.timedelta(days=i)
        device_trend.append({'date': dd.strftime('%m/%d'), 'count': trend_map.get(dd, 0)})

    counts = [x['count'] for x in device_trend]
    trend_spike = None
    if counts:
        mean = sum(counts) / len(counts)
        stdev = _st.pstdev(counts) if len(counts) > 1 else 0
        threshold = max(mean + 2 * stdev, 5)
        for x in device_trend[-7:]:
            if x['count'] >= threshold and x['count'] > mean:
                if trend_spike is None or x['count'] > trend_spike['count']:
                    trend_spike = {'date': x['date'], 'count': x['count'], 'avg': round(mean, 1)}
    return os_distribution, device_trend, trend_spike


def _app_bucket(raw):
    r = str(raw or '').strip()
    if not r:
        return _('Unknown app')
    return r[:24]


def _operator_bucket(raw):
    r = str(raw or '').strip()
    if not r:
        return _('Unknown operator')
    return r[:24]


def _build_more_analytics():
    """watashi v12.3.50dev: app and carrier distribution across the same
    devices the OS chart counts, so the admin can find the one app or the one
    carrier a group of broken reports shares."""
    app_counts = {}
    op_counts = {}
    try:
        from watashipanel.models.hwid import UserHWID
        all_devices = UserHWID.query.all()
    except Exception as e:
        logger.error(f"Error loading devices for app analytics: {e}")
        all_devices = []
    for d in all_devices:
        a = _app_bucket(getattr(d, 'app_name', ''))
        app_counts[a] = app_counts.get(a, 0) + 1
        o = _operator_bucket(getattr(d, 'operator', ''))
        op_counts[o] = op_counts.get(o, 0) + 1
    app_distribution = [{'label': k, 'count': v} for k, v in sorted(app_counts.items(), key=lambda kv: -kv[1])]
    operator_distribution = [{'label': k, 'count': v} for k, v in sorted(op_counts.items(), key=lambda kv: -kv[1])]
    return app_distribution, operator_distribution


def get_user_activity_logs(uuid, user_name):
    """Build the activity feed for one user.

    Watashi v12.2.34: this used to parse the xray access log from disk, which
    reported the addresses a user reached and cost megabytes of reads on
    every open. Only panel-owned facts are reported now: whether the user
    is online, the traffic of the running session, and the last seven
    daily totals. Device statistics live on the monitoring page and are
    built from the device table, never from connection logs.
    """
    logs = []

    try:
        from watashipanel.drivers.xray_api import XrayApi
        from watashipanel.models import DailyUsage
        import datetime

        xray = XrayApi()

        # Get current traffic stats
        if xray.is_enabled():
            try:
                # watashi v12.3.41dev: a peek, not a reset (see XrayApi.get_usage_imp)
                usage = xray.get_usage_imp(uuid, reset=False)
                if usage:
                    logs.append({
                        'time': datetime.datetime.now().strftime('%H:%M:%S'),
                        'type': 'traffic',
                        'message': _('Current session traffic') + f': {format_bytes(usage)}',
                        'details': {'bytes': usage}
                    })
            except Exception:
                pass

        # watashi v12.3.55dev: real, always-fresh figures. The old daily query
        # filtered on DailyUsage.user_uuid, a column the table does not have
        # (its columns are id/date/usage/online/admin_id/child_id), so it died
        # silently and the modal showed nothing. Totals come from the user row
        # — the same source the users page reads.
        try:
            from watashipanel.models import User as UserModel
            u = UserModel.query.filter(UserModel.uuid == uuid).first()
            if u:
                logs.append({
                    'time': datetime.datetime.now().strftime('%H:%M:%S'),
                    'type': 'total_usage',
                    'message': _('Total usage') + f': {format_bytes(int(u.current_usage or 0))}',
                    'details': {'usage': int(u.current_usage or 0)}
                })
        except Exception:
            pass

        # Add connection status from the same system the users page uses
        # (user.last_online within the 120s window), not xray's enabled set.
        try:
            from watashipanel.models import User as UserModel
            u = UserModel.query.filter(UserModel.uuid == uuid).first()
            stamp = getattr(u, 'last_online', None) if u else None
            try:
                seen = max(0.0, (datetime.datetime.now() - stamp).total_seconds()) if stamp is not None and stamp.year > 1 else None
            except (TypeError, ValueError):
                seen = None
            is_online = bool(seen is not None and seen <= ONLINE_WINDOW)
            logs.insert(0, {
                'time': datetime.datetime.now().strftime('%H:%M:%S'),
                'type': 'status',
                'message': _('Currently online') if is_online else _('Currently offline'),
                'details': {'online': is_online}
            })
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error getting user logs for {uuid}: {e}")
        logs.append({
            'time': datetime.datetime.now().strftime('%H:%M:%S') if 'datetime' in dir() else 'now',
            'type': 'error',
            'message': _('Error fetching logs') + f': {str(e)}',
            'details': {}
        })

    return logs


def format_bytes(size):
    """Format bytes to human readable string."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.2f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"
