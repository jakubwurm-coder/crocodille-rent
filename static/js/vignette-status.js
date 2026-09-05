(() => {
  const root = document.getElementById('vignette-sync-data');
  if (!root) return;

  const records = [...root.querySelectorAll('[data-vignette-record]')].map((el) => ({
    id: el.dataset.id || '',
    spz: el.dataset.spz || '',
    status: el.dataset.status || '',
    until: el.dataset.until || '',
    checkedAt: el.dataset.checkedAt || '',
    futureFrom: el.dataset.futureFrom || '',
    source: el.dataset.source || '',
    exempt: el.dataset.exempt === '1'
  }));

  const parseDate = (value) => {
    if (!value) return null;
    const d = new Date(value.length <= 10 ? `${value}T00:00:00` : value);
    return Number.isNaN(d.getTime()) ? null : d;
  };

  const formatDate = (value) => {
    const d = parseDate(value);
    if (!d) return '';
    return new Intl.DateTimeFormat('cs-CZ', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(d);
  };

  const daysLeft = (value) => {
    const d = parseDate(value);
    if (!d) return null;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    d.setHours(0, 0, 0, 0);
    return Math.ceil((d - today) / 86400000);
  };

  const formatChecked = (value) => {
    const d = parseDate(value);
    if (!d) return '';
    const now = new Date();
    const sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    const date = sameDay ? 'dnes' : new Intl.DateTimeFormat('cs-CZ', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(d);
    const time = new Intl.DateTimeFormat('cs-CZ', { hour: '2-digit', minute: '2-digit' }).format(d);
    return `${date} ${time}`;
  };

  const makeMeta = (record) => {
    const checked = formatChecked(record.checkedAt);
    if (!checked) return '';
    return `Ověřeno ${checked}${record.source ? ' · eDalnice' : ''}`;
  };

  const enhanceDetail = (record) => {
    const cards = [...document.querySelectorAll('.status-card')];
    const card = cards.find((item) => {
      const label = item.querySelector('strong');
      return label && label.textContent.trim().toUpperCase() === 'DÁLNIČNÍ ZNÁMKA';
    });
    if (!card) return;

    card.classList.add('vignette-live-card');
    const textWrap = card.querySelector('div');
    const pill = card.querySelector('.pill');
    const oldValue = textWrap ? textWrap.querySelector('span') : null;
    if (!textWrap) return;

    let headline = 'Neověřeno';
    let detail = '';
    let state = 'unknown';

    if (record.exempt || record.status === 'exempt') {
      headline = 'Vozidlo je osvobozeno';
      detail = 'Dálniční známka není vyžadována';
      state = 'ok';
    } else if (record.status === 'valid' && record.until) {
      const days = daysLeft(record.until);
      headline = `Platná do ${formatDate(record.until)}`;
      detail = days === 0 ? 'Končí dnes' : days === 1 ? 'Zbývá 1 den' : days > 1 ? `Zbývá ${days} dní` : 'Platnost skončila';
      state = days != null && days <= 14 ? 'bad' : days != null && days <= 45 ? 'soon' : 'ok';
    } else if (record.status === 'future' && record.futureFrom) {
      headline = `Začíná ${formatDate(record.futureFrom)}`;
      detail = 'Známka je zakoupená, zatím ještě neplatí';
      state = 'soon';
    } else if (record.status === 'missing') {
      headline = 'Dálniční známka nenalezena';
      detail = 'V eDalnice není evidována platná známka';
      state = 'bad';
    } else if (record.until) {
      const days = daysLeft(record.until);
      headline = `Platná do ${formatDate(record.until)}`;
      detail = days != null && days >= 0 ? `Zbývá ${days} dní` : '';
      state = days != null && days <= 14 ? 'bad' : days != null && days <= 45 ? 'soon' : 'ok';
    }

    if (oldValue) oldValue.remove();
    [...textWrap.querySelectorAll('.vignette-live-main,.vignette-live-sub,.vignette-live-meta')].forEach((el) => el.remove());

    const main = document.createElement('span');
    main.className = 'vignette-live-main';
    main.textContent = headline;
    textWrap.appendChild(main);

    if (detail) {
      const sub = document.createElement('small');
      sub.className = 'vignette-live-sub';
      sub.textContent = detail;
      textWrap.appendChild(sub);
    }

    const metaText = makeMeta(record);
    if (metaText) {
      const meta = document.createElement('small');
      meta.className = 'vignette-live-meta';
      meta.textContent = metaText;
      textWrap.appendChild(meta);
    }

    if (pill) {
      pill.classList.remove('ok', 'soon', 'bad', 'unknown');
      pill.classList.add(state);
      pill.textContent = state === 'bad' ? 'POZOR' : state === 'soon' ? 'BRZY' : state === 'ok' ? 'OK' : 'NEOVĚŘENO';
    }
  };

  const enhanceOverviewCard = (record) => {
    if (!record.id) return;
    const link = [...document.querySelectorAll('.vehicle-card')].find((el) => (el.getAttribute('href') || '') === `/v/${record.id}`);
    if (!link) return;

    const statusArea = link.querySelector('.vehicle-card-status');
    if (!statusArea) return;

    if (record.status !== 'missing' && record.status !== 'future') return;

    const ok = statusArea.querySelector('.status-ok');
    if (ok) ok.remove();

    let badges = statusArea.querySelector('.status-badges');
    if (!badges) {
      badges = document.createElement('div');
      badges.className = 'status-badges';
      statusArea.appendChild(badges);
    }

    const already = [...badges.querySelectorAll('.status-badge')].some((el) => el.textContent.toLowerCase().includes('dálniční známka'));
    if (already) return;

    const badge = document.createElement('span');
    badge.className = `status-badge ${record.status === 'missing' ? 'bad' : 'soon'}`;
    badge.textContent = record.status === 'missing'
      ? 'Dálniční známka · nenalezena'
      : `Dálniční známka · od ${formatDate(record.futureFrom) || 'budoucí'}`;
    badges.appendChild(badge);
  };

  if (document.querySelector('.vehicle-screen')) {
    enhanceDetail(records[0] || {});
  } else {
    records.forEach(enhanceOverviewCard);
  }
})();
