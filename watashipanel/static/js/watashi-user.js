// watashi v12.3.49dev: the user page script, extracted from watashi_user.html.
// WS_USER_DATA (TXT + CARDS) is printed by the template as a small inline boot
// object; the rest ships from here with a content seal and a year of caching.
(function () {
var TXT = (window.WS_USER_DATA && window.WS_USER_DATA.TXT) || {};
var CARDS = (window.WS_USER_DATA && window.WS_USER_DATA.CARDS) || [];
var CARD_BY_KEY = {};
for (var ci = 0; ci < CARDS.length; ci++) { CARD_BY_KEY[CARDS[ci].key] = CARDS[ci]; }

// A card carries either a link or the text of a file, never both, so
// one reader serves copy, code and download alike.
function cardOf(el) { return CARD_BY_KEY[el.getAttribute('data-card')] || null; }
function cardBody(c) { return (c && (c.link || c.text)) || ''; }
var SKIN_KEY = 'watashi-user-skin';

function paintSkin(name) {
	var root = document.documentElement;
	root.setAttribute('data-theme', name);
	var tone = document.getElementById('np-tone');
	if (tone) { tone.setAttribute('content', name === 'light' ? '#eef2f6' : (tone.getAttribute('data-dark') || '#0b0f19')); } // watashi v12.3.44dev: the dark of the palette
	var knob = document.getElementById('np-skin');
	if (knob) {
		var sign = knob.querySelector('i');
		if (sign) { sign.className = name === 'light' ? 'fa-solid fa-sun' : 'fa-solid fa-moon'; }
	}
}

try {
	var kept = localStorage.getItem(SKIN_KEY);
	paintSkin(kept === 'light' ? 'light' : (kept === 'dark' ? 'dark' : document.documentElement.getAttribute('data-theme') || 'dark'));
} catch (e) { paintSkin('dark'); }

var skinBtn = document.getElementById('np-skin');
if (skinBtn) {
	skinBtn.addEventListener('click', function () {
		var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
		paintSkin(next);
		try { localStorage.setItem(SKIN_KEY, next); } catch (e) {}
	});
}

var againBtn = document.getElementById('np-again');
if (againBtn) {
	againBtn.addEventListener('click', function () {
		againBtn.classList.add('spin');
		window.location.reload();
	});
}

function toast(words, bad) {
	var host = document.getElementById('np-toasts');
	if (!host) { return; }
	var note = document.createElement('div');
	note.className = 'np-toast' + (bad ? ' bad' : '');
	var sign = document.createElement('i');
	sign.className = bad ? 'fa-solid fa-circle-exclamation' : 'fa-solid fa-circle-check';
	var say = document.createElement('span');
	say.textContent = words;
	note.appendChild(sign);
	note.appendChild(say);
	host.appendChild(note);
	setTimeout(function () {
		note.style.opacity = '0';
		note.style.transform = 'translateY(10px)';
		note.style.transition = 'all .3s ease';
		setTimeout(function () { if (note.parentNode) { note.parentNode.removeChild(note); } }, 320);
	}, 2600);
}

function copyText(text) {
	if (!text) { return Promise.reject(); }
	if (navigator.clipboard && navigator.clipboard.writeText) {
		return navigator.clipboard.writeText(text);
	}
	return new Promise(function (done, fail) {
		try {
			var pad = document.createElement('textarea');
			pad.value = text;
			pad.setAttribute('readonly', 'readonly');
			pad.style.position = 'fixed';
			pad.style.opacity = '0';
			document.body.appendChild(pad);
			pad.select();
			var won = document.execCommand('copy');
			document.body.removeChild(pad);
			won ? done() : fail();
		} catch (e) { fail(); }
	});
}

document.addEventListener('click', function (event) {
	var big = event.target.closest ? event.target.closest('.np-copy') : null;
	if (big) {
		copyText(big.getAttribute('data-link')).then(function () {
			big.classList.add('copied');
			setTimeout(function () { big.classList.remove('copied'); }, 2000);
			toast(TXT.copiedMain);
		}, function () { toast(TXT.copyFailed, true); });
		return;
	}
	// watashi v12.2.130ao: one handler per button kind, for every card, so a
	// new kind of config needs no new javascript.
	var cCopy = event.target.closest ? event.target.closest('.np-cd') : null;
	if (cCopy) {
		var cc = cardOf(cCopy);
		copyText(cardBody(cc)).then(function () {
			var sign = cCopy.querySelector('i');
			cCopy.classList.add('copied');
			if (sign) { sign.className = 'fa-solid fa-check'; }
			setTimeout(function () {
				cCopy.classList.remove('copied');
				if (sign) { sign.className = 'fa-regular fa-copy'; }
			}, 2000);
			toast(TXT.copied);
		}, function () { toast(TXT.copyFailed, true); });
		return;
	}
	var cCode = event.target.closest ? event.target.closest('.np-cq') : null;
	if (cCode) {
		var cq = cardOf(cCode);
		if (cq) { openQr(cardBody(cq), cq.name); }
		return;
	}
	// A file is handed over from the text the page already carries. A
	// subscription is handed over by asking its own address for it,
	// which is the same origin, so the download attribute is honoured.
	var cFile = event.target.closest ? event.target.closest('.np-cf') : null;
	if (cFile) {
		var cf = cardOf(cFile);
		if (cf) {
			var a = document.createElement('a');
			var made = '';
			if (cf.text) {
				made = URL.createObjectURL(new Blob([cf.text], { type: 'text/plain' }));
				a.href = made;
			} else {
				a.href = cf.link;
			}
			a.download = cf.file || 'config.txt';
			document.body.appendChild(a); a.click(); a.remove();
			if (made) { setTimeout(function () { URL.revokeObjectURL(made); }, 2000); }
			toast(TXT.fileSaved || TXT.copied);
		}
		return;
	}
	var shot = event.target.closest ? event.target.closest('.np-qr') : null;
	if (shot) {
		openQr(shot.getAttribute('data-link'), shot.getAttribute('data-title') || 'Auto Connect');
	}
});

function drawQr(host, text) {
	host.innerHTML = '';
	// watashi v12.2.130ao: a subscription address is short and a file is not.
	// A long payload gets the larger box, the forgiving error level and
	// a canvas drawn well above the size it is shown at, so scaling it
	// down leaves crisp modules instead of two soft pixels each.
	var body = String(text || '');
	var roomy = body.length > 200;
	host.classList.toggle('big', roomy);
	try {
		if (window.QRCode) {
			new QRCode(host, {
				text: body,
				width: roomy ? 520 : 240,
				height: roomy ? 520 : 240,
				colorDark: '#0b0f19',
				colorLight: '#ffffff',
				correctLevel: roomy ? QRCode.CorrectLevel.L : QRCode.CorrectLevel.M
			});
			return true;
		}
	} catch (e) {}
	host.classList.remove('big');
	return false;
}

function openQr(link, title) {
	var veil = document.getElementById('np-veil');
	var host = document.getElementById('np-veil-qr');
	var head = document.getElementById('np-veil-t');
	if (!veil || !host) { return; }
	if (head) { head.textContent = title; }
	if (!drawQr(host, link)) {
		toast(TXT.qrFailed, true);
		return;
	}
	veil.classList.add('show');
}

function shutQr() {
	var veil = document.getElementById('np-veil');
	if (veil) { veil.classList.remove('show'); }
}

function qrPicture() {
	var host = document.getElementById('np-veil-qr');
	if (!host) { return Promise.reject(); }
	var board = host.querySelector('canvas');
	if (board && board.toBlob) {
		return new Promise(function (done, fail) {
			try {
				board.toBlob(function (lump) { lump ? done(lump) : fail(); }, 'image/png');
			} catch (e) { fail(); }
		});
	}
	var pic = host.querySelector('img');
	if (pic && pic.src && window.fetch) {
		return fetch(pic.src).then(function (answer) { return answer.blob(); });
	}
	return Promise.reject();
}

var picBtn = document.getElementById('np-veil-cp');
if (picBtn) {
	picBtn.addEventListener('click', function () {
		if (!navigator.clipboard || !window.ClipboardItem) {
			toast(TXT.picFailed, true);
			return;
		}
		qrPicture().then(function (lump) {
			var bag = {};
			bag['image/png'] = lump;
			return navigator.clipboard.write([new ClipboardItem(bag)]);
		}).then(function () {
			picBtn.classList.add('copied');
			setTimeout(function () { picBtn.classList.remove('copied'); }, 2000);
			toast(TXT.picCopied);
		}).catch(function () { toast(TXT.picFailed, true); });
	});
}

var shutBtn = document.getElementById('np-veil-x');
if (shutBtn) { shutBtn.addEventListener('click', shutQr); }
var veilBox = document.getElementById('np-veil');
if (veilBox) {
	veilBox.addEventListener('click', function (event) { if (event.target === this) { shutQr(); } });
}
// watashi v12.2.130ck: the temporary short link. The panel hands back a code
// and the seconds it has left, and the ring drains against a deadline taken
// once, so a slow tick or a sleeping tab cannot make the clock lie.
(function shortLink() {
	var card = document.getElementById('np-short');
	if (!card) { return; }
	var api = card.getAttribute('data-api') || '';
	var make = document.getElementById('np-short-make');
	var live = document.getElementById('np-short-live');
	var gone = document.getElementById('np-short-gone');
	var arc = document.getElementById('np-short-arc');
	var clock = document.getElementById('np-short-clock');
	var seat = document.getElementById('np-short-url');
	var cp = card.querySelector('.np-short-cp');
	var qr = card.querySelector('.np-qr');
	if (!api || !make || !live || !arc) { return; }
	var ROUND = 2 * Math.PI * 19;
	arc.style.strokeDasharray = ROUND;
	var ends = 0, span = 300, beat = null, busy = false;

	function paint() {
		var left = Math.max(0, Math.ceil((ends - Date.now()) / 1000));
		var min = Math.floor(left / 60), sec = left % 60;
		clock.textContent = min + ':' + (sec < 10 ? '0' : '') + sec;
		arc.style.strokeDashoffset = ROUND * (1 - (span > 0 ? left / span : 0));
		card.classList.toggle('is-late', left > 0 && left <= 60);
		if (left <= 0) { fade(); }
	}

	function fade() {
		if (beat) { clearInterval(beat); beat = null; }
		live.hidden = true;
		gone.hidden = false;
		card.classList.remove('is-live', 'is-late');
	}

	make.addEventListener('click', function () {
		if (busy) { return; }
		busy = true;
		card.classList.add('is-busy');
		fetch(api, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
			.then(function (answer) {
				if (!answer.ok) { throw new Error('short'); }
				return answer.json();
			})
			.then(function (body) {
				var where = (body && body.full_url) || '';
				var life = parseInt((body && body.expire_in) || 0, 10);
				if (!where) { throw new Error('short'); }
				span = life > 0 ? life : 300;
				ends = Date.now() + span * 1000;
				seat.textContent = where;
				seat.setAttribute('title', where);
				if (cp) { cp.setAttribute('data-link', where); }
				if (qr) { qr.setAttribute('data-link', where); }
				gone.hidden = true;
				live.hidden = false;
				card.classList.add('is-live');
				paint();
				if (beat) { clearInterval(beat); }
				beat = setInterval(paint, 500);
				toast(TXT.shortMade);
			})
			.catch(function () { toast(TXT.shortFailed, true); })
			.then(function () {
				busy = false;
				card.classList.remove('is-busy');
			});
	});

	if (cp) {
		cp.addEventListener('click', function () {
			copyText(cp.getAttribute('data-link')).then(function () {
				cp.classList.add('copied');
				setTimeout(function () { cp.classList.remove('copied'); }, 2000);
				toast(TXT.shortCopied);
			}, function () { toast(TXT.copyFailed, true); });
		});
	}
})();

// watashi v12.2.130cj: the Auto Connect popup. Opening it is plain, and a tap
// on an app only sets the address of that app, which the phone or the desktop
// hands to the app itself. If the app is missing nothing happens, which is why
// every row also carries the download arrow.
function openApps() {
	var box = document.getElementById('np-apps');
	if (box) { box.classList.add('show'); }
}

function shutApps() {
	var box = document.getElementById('np-apps');
	if (box) { box.classList.remove('show'); }
}

var appsOpen = document.getElementById('np-auto-url');
if (appsOpen && appsOpen.tagName === 'BUTTON') { appsOpen.addEventListener('click', openApps); }
var appsBtn = document.getElementById('np-connect');
if (appsBtn) { appsBtn.addEventListener('click', openApps); }
var appsShut = document.getElementById('np-apps-x');
if (appsShut) { appsShut.addEventListener('click', shutApps); }
var appsBox = document.getElementById('np-apps');
if (appsBox) {
	appsBox.addEventListener('click', function (event) { if (event.target === this) { shutApps(); } });
}
document.querySelectorAll('.apps-tab').forEach(function (tab) {
	tab.addEventListener('click', function () {
		document.querySelectorAll('.apps-tab').forEach(function (one) { one.classList.remove('active'); });
		document.querySelectorAll('.apps-pane').forEach(function (one) { one.classList.remove('active'); });
		tab.classList.add('active');
		var pane = document.getElementById('apps-' + tab.getAttribute('data-apps'));
		if (pane) { pane.classList.add('active'); }
	});
});
document.querySelectorAll('.apps-go').forEach(function (one) {
	one.addEventListener('click', function () {
		var deep = one.getAttribute('data-deep') || '';
		if (!deep) { return; }
		try { window.location.href = deep; } catch (e) {}
	});
});

document.addEventListener('keydown', function (event) {
	if (event.key !== 'Escape') { return; }
	var box = document.getElementById('np-apps');
	if (box && box.classList.contains('show')) { shutApps(); return; }
	shutQr();
});

document.querySelectorAll('.setup-tab').forEach(function (tab) {
	tab.addEventListener('click', function () {
		document.querySelectorAll('.setup-tab').forEach(function (one) { one.classList.remove('active'); });
		document.querySelectorAll('.setup-content').forEach(function (one) { one.classList.remove('active'); });
		tab.classList.add('active');
		var pane = document.getElementById('tab-' + tab.getAttribute('data-tab'));
		if (pane) { pane.classList.add('active'); }
	});
});

(function () {
	var ring = document.getElementById('np-live');
	var label = document.getElementById('np-pct');
	if (!ring) { return; }
	var pct = parseFloat(ring.getAttribute('data-pct') || '0');
	if (isNaN(pct) || pct < 0) { pct = 0; }
	if (pct > 100) { pct = 100; }
	var round = 2 * Math.PI * 65;
	setTimeout(function () { ring.style.strokeDashoffset = round - (round * pct / 100); }, 500);
	if (!label) { return; }
	var step = 0;
	var timer = setInterval(function () {
		step += Math.max(0.4, pct / 40);
		if (step >= pct) { step = pct; clearInterval(timer); }
		label.textContent = (Math.round(step * 10) / 10) + '%';
	}, 40);
})();

/* watashi v12.3.49dev: the custom cursor logic (dot + ring + the permanent
   requestAnimationFrame easing loop) was removed together with its CSS; the
   page uses the native cursor like the refreshed admin pages. */

/* watashi v12.3.49dev: the overlay scrollbar engine (a Map of bars, a
   permanent requestAnimationFrame tick, and a setInterval DOM scan every
   3s) was removed; the page now uses the native scrollbar, matching the
   refreshed admin pages. The old WS-SHARED css block above was removed
   with it (it only hid the native scrollbar for the overlay). */

})();
