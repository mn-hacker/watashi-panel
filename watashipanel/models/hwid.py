# watashi v12.3.40dev: the old project name was taken out of this file (names, paths and links only)
import datetime

from watashipanel.database import db


class UserHWID(db.Model):
    """Hardware/device identifiers (HWID) reported by client apps for a user."""
    __tablename__ = 'user_hwid'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    hwid = db.Column(db.String(256), nullable=False)
    device_os = db.Column(db.String(64), default='', nullable=False)
    ver_os = db.Column(db.String(64), default='', nullable=False)
    device_model = db.Column(db.String(128), default='', nullable=False)
    # watashi v12.3.50dev: the app that reported the id (Happ, Hiddify, v2box,
    # ...) and the carrier the device is on, for the monitoring page
    app_name = db.Column(db.String(64), default='', nullable=False)
    operator = db.Column(db.String(64), default='', nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.now)
    last_seen = db.Column(db.DateTime, nullable=False, default=datetime.datetime.now)

    __table_args__ = (db.UniqueConstraint('user_id', 'hwid', name='uq_user_hwid'),)


def get_user_hwids(user_id):
    return UserHWID.query.filter(UserHWID.user_id == user_id).order_by(UserHWID.last_seen.desc()).all()


def get_user_hwid_count(user_id):
    return UserHWID.query.filter(UserHWID.user_id == user_id).count()


def get_user_hwid_by_value(user_id, hwid):
    return UserHWID.query.filter(UserHWID.user_id == user_id, UserHWID.hwid == hwid).first()


class UserUsageDay(db.Model):
    """watashi v12.3.64dev: per-user daily usage snapshot, one row per user per
    day. DailyUsage is admin-wide and has no user column, so the monitoring
    modal's 7-day wave needs its own table. The usage collector upserts
    today's row (usage_total = the user's cumulative current_usage at that
    moment); the modal turns consecutive totals into per-day deltas."""
    __tablename__ = 'user_usage_day'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    day = db.Column(db.Date, nullable=False, index=True)
    usage_total = db.Column(db.BigInteger, default=0, nullable=False)

    __table_args__ = (db.UniqueConstraint('user_id', 'day', name='uq_user_usage_day'),)


def snapshot_user_usage(user_id, usage_total, day=None):
    """Upsert today's cumulative-usage snapshot for one user (idempotent)."""
    import datetime as _dt
    d = day or _dt.date.today()
    row = UserUsageDay.query.filter(UserUsageDay.user_id == user_id, UserUsageDay.day == d).first()
    if row:
        if usage_total > row.usage_total:
            row.usage_total = int(usage_total)
    else:
        db.session.add(UserUsageDay(user_id=user_id, day=d, usage_total=int(usage_total)))


def register_user_hwid(user_id, hwid, device_os='', ver_os='', device_model='',
                       app_name='', operator='', commit=True):
    now = datetime.datetime.now()
    rec = get_user_hwid_by_value(user_id, hwid)
    if rec:
        rec.last_seen = now
        if device_os:
            rec.device_os = device_os[:64]
        if ver_os:
            rec.ver_os = ver_os[:64]
        if device_model:
            rec.device_model = device_model[:128]
        if app_name:
            rec.app_name = app_name[:64]
        if operator:
            rec.operator = operator[:64]
    else:
        rec = UserHWID(
            user_id=user_id,
            hwid=(hwid or '')[:256],
            device_os=(device_os or '')[:64],
            ver_os=(ver_os or '')[:64],
            device_model=(device_model or '')[:128],
            app_name=(app_name or '')[:64],
            operator=(operator or '')[:64],
            created_at=now,
            last_seen=now,
        )
        db.session.add(rec)
    if commit:
        db.session.commit()
    return rec


def delete_user_hwid(user_id, hwid, commit=True):
    rec = get_user_hwid_by_value(user_id, hwid)
    if rec:
        db.session.delete(rec)
        if commit:
            db.session.commit()
        return True
    return False


def reset_user_hwids(user_id, commit=True):
    n = UserHWID.query.filter(UserHWID.user_id == user_id).delete()
    if commit:
        db.session.commit()
    return n


def upsert_user_hwid(user_id, hwid, device_os='', ver_os='', device_model='',
                     app_name='', operator='', commit=True):
    """watashi v12.3.50dev: remember a device even when no limit applies. The
    row is the same the limit keeps, so the monitoring page sees every app
    that ever asked, not only the flagged users; it carries no connection
    state and never blocks anybody."""
    if not hwid:
        return None
    return register_user_hwid(user_id, hwid, device_os=device_os, ver_os=ver_os,
                              device_model=device_model, app_name=app_name,
                              operator=operator, commit=commit)
