  // ── State for the day-carousel and Leaflet map ──
  let _days         = [];
  let _activeDay    = 0;
  let _dayMap       = null;
  let _dayMapOpen   = false;
  let _routingCtrl  = null;
  let _departureCity = '';

  // Create a circular Leaflet div-icon with a label and colour
  function _makeMarkerIcon(label, color, size) {
    size = size || 30;
    return L.divIcon({
      html: `<div style="background:${color};color:#fff;width:${size}px;height:${size}px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:${Math.round(size*0.42)}px;border:2px solid rgba(255,255,255,0.9);box-shadow:0 2px 8px rgba(0,0,0,0.45);">${label}</div>`,
      className:'', iconSize:[size,size], iconAnchor:[size/2,size/2],
    });
  }

  const STEP_ICONS = {
    Head:'→', Straight:'↑', TurnLeft:'←', TurnRight:'→',
    SlightLeft:'↖', SlightRight:'↗', SharpLeft:'↰', SharpRight:'↱',
    Uturn:'↩', Roundabout:'⟳', DestinationReached:'✦', WaypointReached:'◉',
  };
  function _fmtDist(m) { return m >= 1000 ? (m/1000).toFixed(1)+' км' : Math.round(m)+' м'; }

  // Collect morning/afternoon/evening + restaurant map points for a day object
  function _collectDayPts(day) {
    const placePts = [];
    if (day.morning_lat   && day.morning_lng)   placePts.push({lat:day.morning_lat,  lng:day.morning_lng,  label:'☀', color:'#c4a052', size:32, title:day.morning_place  ||'Ранок', desc:day.morning  ||''});
    if (day.afternoon_lat && day.afternoon_lng) placePts.push({lat:day.afternoon_lat,lng:day.afternoon_lng,label:'◑', color:'#c45c3a', size:32, title:day.afternoon_place||'День',  desc:day.afternoon||''});
    if (day.evening_lat   && day.evening_lng)   placePts.push({lat:day.evening_lat,  lng:day.evening_lng,  label:'☽', color:'#5a7a5c', size:32, title:day.evening_place  ||'Вечір', desc:day.evening  ||''});
    if (!placePts.length && day.lat && day.lng) placePts.push({lat:day.lat,lng:day.lng,label:day.day||'●',color:'#c4a052',size:32,title:day.title||'',desc:day.location||''});
    const restPts = (day.restaurants||[]).filter(r=>r.lat&&r.lng)
      .map(r=>({lat:r.lat,lng:r.lng,label:'🍽',color:'#3d6b5e',size:28,title:r.name,desc:`${r.cuisine||''}${r.price?' · '+r.price:''}`}));
    return { placePts, restPts, allPts:[...placePts,...restPts] };
  }

  // Render or refresh the Leaflet map panel for the given day
  function _renderLeafletMap(day) {
    const { placePts, allPts } = _collectDayPts(day);
    if (!allPts.length) return;

    if (!_dayMap) {
      _dayMap = L.map('dayMap').setView([allPts[0].lat, allPts[0].lng], 13);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {attribution:'© OpenStreetMap'}).addTo(_dayMap);
    } else {
      if (_routingCtrl) { _routingCtrl.remove(); _routingCtrl = null; }
      _dayMap.eachLayer(l => { if (!(l instanceof L.TileLayer)) _dayMap.removeLayer(l); });
    }

    const lls = [];
    allPts.forEach(p => {
      L.marker([p.lat,p.lng], {icon:_makeMarkerIcon(p.label,p.color,p.size)})
        .addTo(_dayMap)
        .bindPopup(`<strong style="color:${p.color}">${p.title}</strong><br><small style="opacity:.8">${p.desc.substring(0,110)}${p.desc.length>110?'…':''}</small>`);
      lls.push([p.lat,p.lng]);
    });

    if (placePts.length >= 2) {
      _routingCtrl = L.Routing.control({
        waypoints: placePts.map(p => L.latLng(p.lat, p.lng)),
        router: L.Routing.osrmv1({ serviceUrl:'https://router.project-osrm.org/route/v1', profile:'foot' }),
        routeWhileDragging: false, show: false, addWaypoints: false, fitSelectedRoutes: false,
        lineOptions: { styles:[{color:'#c4a052',weight:3.5,opacity:0.8}], extendToWaypoints:false, missingRouteTolerance:10 },
        createMarker: () => null,
      }).addTo(_dayMap);

      _routingCtrl.on('routingerror', () => {
        L.polyline(placePts.map(p=>[p.lat,p.lng]),{color:'#c4a052',weight:2.5,opacity:0.65,dashArray:'6,8'}).addTo(_dayMap);
      });
    }

    if (lls.length>1) _dayMap.fitBounds(lls,{padding:[45,45]});
    else _dayMap.setView(lls[0],15);
    setTimeout(()=>_dayMap&&_dayMap.invalidateSize(),480);
  }

  function _renderDayMap(day) {
    if (!day || !_dayMapOpen) return;
    _renderLeafletMap(day);
  }

  // Try up to 4 Wikipedia API strategies to find a thumbnail for a place name
  async function _getWikiPhoto(placeName, cityContext) {
    if (!placeName) return null;

    async function _wikiQuery(query, useSearch) {
      try {
        let url;
        if (useSearch) {
          url = `https://en.wikipedia.org/w/api.php?action=query&generator=search&gsrsearch=${encodeURIComponent(query)}&gsrlimit=3&prop=pageimages&format=json&pithumbsize=800&origin=*`;
        } else {
          url = `https://en.wikipedia.org/w/api.php?action=query&titles=${encodeURIComponent(query)}&prop=pageimages&format=json&pithumbsize=800&origin=*`;
        }
        const res = await fetch(url);
        const data = await res.json();
        const pages = Object.values(data?.query?.pages || {});
        for (const page of pages) {
          if (page.thumbnail?.source && !page.missing) return page.thumbnail.source;
        }
      } catch {}
      return null;
    }

    // 1. exact title
    let src = await _wikiQuery(placeName, false);
    if (src) return src;

    // 2. title with city context
    if (cityContext) {
      src = await _wikiQuery(`${placeName} ${cityContext}`, false);
      if (src) return src;
    }

    // 3. full-text search (bare name)
    src = await _wikiQuery(placeName, true);
    if (src) return src;

    // 4. full-text search with city context
    if (cityContext) {
      src = await _wikiQuery(`${placeName} ${cityContext}`, true);
      if (src) return src;
    }

    return null;
  }

  // Build the HTML card for one time-of-day block (morning/afternoon/evening)
  function _placeBlockHtml(timeKey, label, badgeClass, day) {
    const place  = day[`${timeKey}_place`]  || '';
    const short  = day[timeKey]             || '';
    const detail = day[`${timeKey}_detail`] || '';
    const tips   = day[`${timeKey}_tips`]   || '';
    if (!short && !place) return '';
    return `
      <div class="place-block">
        <div class="place-photo-wrap" id="wrap-place-photo-${timeKey}">
          <div class="place-photo-placeholder">📍</div>
        </div>
        <div class="place-text">
          <div class="place-block-header" style="padding:0;margin-bottom:0.3rem;">
            <span class="place-time-badge ${badgeClass}">${label}</span>
            ${place ? `<span class="place-name">${place}</span>` : ''}
          </div>
          ${short  ? `<div class="place-short">${short}</div>`   : ''}
          ${detail ? `<div class="place-detail">${detail}</div>` : ''}
          ${tips   ? `<div class="place-tips">💡 ${tips}</div>`  : ''}
        </div>
      </div>`;
  }

  // Load a place photo: tries Google Places proxy first, falls back to Wikipedia
  function _loadPlacePhoto(timeKey, placeName, cityContext) {
    if (!placeName) return;
    const wrap = () => document.getElementById(`wrap-place-photo-${timeKey}`);
    const setPhoto = (src) => {
      const el = wrap();
      if (!el) return;
      el.innerHTML = `<img class="place-photo" src="${src}" alt="${placeName}"
        onerror="this.parentElement.innerHTML='<div class=place-photo-placeholder>📍</div>'">`;
    };

    // 1. Try Google Places via backend proxy
    const gpUrl = '/api/place-photo?place=' + encodeURIComponent(placeName)
                + (cityContext ? '&city=' + encodeURIComponent(cityContext) : '');
    fetch(gpUrl, {method:'HEAD'}).then(r => {
      if (r.ok) { setPhoto(gpUrl); return; }
      // 2. Fallback to Wikipedia
      _getWikiPhoto(placeName, cityContext).then(src => { if (src) setPhoto(src); });
    }).catch(() => {
      _getWikiPhoto(placeName, cityContext).then(src => { if (src) setPhoto(src); });
    });
  }

  // Build a Google Maps directions URL from the day's named places
  function _buildGmapsUrl(day) {
    const city = day.location || '';
    const _q = (name) => (name && city) ? `${name}, ${city}` : (name || '');

    const names = [
      _q(day.morning_place),
      _q(day.afternoon_place),
      _q(day.evening_place),
    ].filter(Boolean);

    if (!names.length) {
      const lat = day.lat || day.morning_lat || day.afternoon_lat;
      const lng = day.lng || day.morning_lng || day.afternoon_lng;
      return (lat && lng) ? `https://www.google.com/maps/search/?api=1&query=${lat},${lng}` : null;
    }

    // Path-based format /dir/A/B/C/ is what Google Maps uses internally and
    // resolves named places far more reliably than the ?api=1&origin= param format.
    const parts = names.map(n => encodeURIComponent(n)).join('/');
    return `https://www.google.com/maps/dir/${parts}/`;
  }

  // Render the detail panel for day at carousel index and refresh the map if open
  function showDay(index) {
    if (index < 0 || index >= _days.length) return;
    _activeDay = index;
    const day = _days[index];

    document.querySelectorAll('.day-tab').forEach((t,i) => t.classList.toggle('active', i===index));

    const hasMap = (day.morning_lat&&day.morning_lng)||(day.afternoon_lat&&day.afternoon_lng)||(day.evening_lat&&day.evening_lng)||(day.lat&&day.lng);
    const gmapsUrl = _buildGmapsUrl(day);
    document.getElementById('dayDetailPanel').innerHTML = `
      <div class="day-detail-header">
        <div class="day-number">ДЕНЬ ${day.day || index+1}</div>
        <div class="day-title">${day.title || '—'}</div>
        ${day.location ? `<div class="day-location-tag">📍 ${day.location}</div>` : ''}
      </div>
      <div class="day-times-row">
        ${_placeBlockHtml('morning',   '☀ Ранок',  'morning',   day)}
        <div class="times-divider"></div>
        ${_placeBlockHtml('afternoon', '◑ День',   'afternoon', day)}
        <div class="times-divider"></div>
        ${_placeBlockHtml('evening',   '☽ Вечір',  'evening',   day)}
      </div>
      ${day.food_tip ? `<div class="food-tip">🍽 ${day.food_tip}</div>` : ''}
      ${_buildRestaurantsHtml(day.restaurants || [])}
      ${day.estimated_cost ? `<div class="day-cost">≈ ${day.estimated_cost}</div>` : ''}
      <div class="day-map-actions">
        ${hasMap ? `<button class="day-map-btn" id="dayMapBtn" onclick="toggleDayMap()">🗺 Карта дня</button>` : ''}
        ${gmapsUrl ? `<a class="day-map-btn" href="${gmapsUrl}" target="_blank" rel="noopener">↗ Маршрут у Google Maps</a>` : ''}
      </div>
    `;

    const cityCtx = day.location || '';
    _loadPlacePhoto('morning',   day.morning_place,   cityCtx);
    _loadPlacePhoto('afternoon', day.afternoon_place, cityCtx);
    _loadPlacePhoto('evening',   day.evening_place,   cityCtx);

    if (_dayMapOpen) {
      const btn = document.getElementById('dayMapBtn');
      if (btn) btn.classList.add('open');
      _renderDayMap(day);
    }
  }

  // Show or hide the Leaflet map panel below the day detail
  function toggleDayMap() {
    const section  = document.getElementById('dayMapSection');
    const btn      = document.getElementById('dayMapBtn');
    _dayMapOpen = !_dayMapOpen;
    section.classList.toggle('visible', _dayMapOpen);
    if (btn) btn.classList.toggle('open', _dayMapOpen);
    if (!_dayMapOpen) {
      stepsEl.style.display  = 'none';
      summaryEl.style.display = 'none';
    } else {
      setTimeout(() => _renderDayMap(_days[_activeDay]), 50);
    }
  }

  // Initialise the day-tab carousel with the AI-generated days array
  function initCarousel(days) {
    _days       = days;
    _activeDay  = 0;
    _dayMapOpen = false;
    if (_routingCtrl) { _routingCtrl.remove(); _routingCtrl = null; }
    if (_dayMap) { _dayMap.remove(); _dayMap = null; }
    document.getElementById('dayMapSection').classList.remove('visible');

    document.getElementById('daysTabs').innerHTML = days.map((d, i) => `
      <button class="day-tab${i===0?' active':''}" onclick="showDay(${i})">
        <div class="day-tab-num">ДЕНЬ ${d.day || i+1}</div>
        <div class="day-tab-title">${d.title || '—'}</div>
      </button>`).join('');

    document.getElementById('daysLayout').style.display = 'grid';
    showDay(0);
  }

  // Switch between the three SPA pages: welcome, search, results
  function showPage(page) {
    document.getElementById('pageWelcome').style.display = page === 'welcome' ? 'flex' : 'none';
    document.querySelector('.container').style.display   = page !== 'welcome'  ? ''     : 'none';
    document.getElementById('pageSearch').style.display  = page === 'search'   ? ''     : 'none';
    document.getElementById('pageResults').style.display = page === 'results'  ? ''     : 'none';
    window.scrollTo(0, 0);
  }

  // Show one auth form (login/register/forgot/reset) and hide the others
  function switchWelcomeTab(tab) {
    ['login','register','forgot','reset'].forEach(t => {
      const form = document.getElementById('welcome' + t.charAt(0).toUpperCase() + t.slice(1) + 'Form');
      if (form) form.style.display = t === tab ? 'flex' : 'none';
      const btn = document.getElementById('wtab' + t.charAt(0).toUpperCase() + t.slice(1));
      if (btn) btn.classList.toggle('active', t === tab);
    });
    document.querySelector('.welcome-auth-tabs').style.display = (tab === 'forgot' || tab === 'reset') ? 'none' : 'flex';
    document.getElementById('welcomeError').classList.remove('visible');
    document.getElementById('welcomeSuccess').classList.remove('visible');
  }
  function _showWelcomeError(msg) {
    const e = document.getElementById('welcomeError');
    e.textContent = msg; e.classList.add('visible');
    document.getElementById('welcomeSuccess').classList.remove('visible');
  }
  function _showWelcomeSuccess(html) {
    const e = document.getElementById('welcomeSuccess');
    e.innerHTML = html; e.classList.add('visible');
    document.getElementById('welcomeError').classList.remove('visible');
  }
  async function submitWelcomeLogin(ev) {
    ev.preventDefault();
    const res = await fetch('/api/auth/login', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ email: document.getElementById('wLoginEmail').value, password: document.getElementById('wLoginPassword').value })
    });
    const data = await res.json();
    if (data.success) {
      _currentUser = data.user; _updateHeaderAuth();
      if (_currentUser.interests && _currentUser.interests.length) _applyUserInterests(_currentUser.interests);
      showPage('search');
    } else _showWelcomeError(data.error);
  }
  async function submitWelcomeRegister(ev) {
    ev.preventDefault();
    const pwd  = document.getElementById('wRegPassword').value;
    const conf = document.getElementById('wRegPasswordConfirm').value;
    if (pwd !== conf) { _showWelcomeError('Паролі не збігаються'); return; }
    if (pwd.length < 8) { _showWelcomeError('Пароль мінімум 8 символів'); return; }
    const res = await fetch('/api/auth/register', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ name: document.getElementById('wRegName').value, email: document.getElementById('wRegEmail').value, password: pwd })
    });
    const data = await res.json();
    if (data.success) { _currentUser = data.user; _updateHeaderAuth(); showOnboarding(data.user.name); }
    else _showWelcomeError(data.error);
  }
  async function submitWelcomeForgot(ev) {
    ev.preventDefault();
    const res = await fetch('/api/auth/forgot-password', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ email: document.getElementById('wForgotEmail').value })
    });
    const data = await res.json();
    if (data.success) {
      let html = data.message;
      if (data._dev_token) {
        html += `<br><br><span style="opacity:.7">SMTP не налаштовано — тестове посилання:</span><br>
          <a href="/?reset_token=${data._dev_token}" style="color:var(--gold);font-family:monospace;font-size:0.65rem;word-break:break-all;">Натисни для скидання пароля →</a>`;
      }
      _showWelcomeSuccess(html);
    } else _showWelcomeError(data.error);
  }

  let weatherMode = 'dates';
  let budgetLevel = 'mid';

  // Mark the chosen budget card as active and update the module-level budgetLevel var
  function selectBudgetLevel(level) {
    budgetLevel = level;
    document.querySelectorAll('.budget-level-card').forEach(c => c.classList.remove('active'));
    document.querySelector(`.budget-level-card.${level}`).classList.add('active');
  }

  // Toggle between date-picker mode and weather-preference mode on the search form
  function setWeatherMode(mode) {
    weatherMode = mode;
    document.getElementById('btnModeDates').classList.toggle('active', mode === 'dates');
    document.getElementById('btnModePrefs').classList.toggle('active', mode === 'prefs');
    document.getElementById('weatherDates').classList.toggle('visible', mode === 'dates');
    document.getElementById('weatherPrefs').classList.toggle('visible', mode === 'prefs');
  }

function renderWeather(weather, weatherType, route) {
  const section = document.getElementById('weatherSection');
  const content = document.getElementById('weatherContent');
  const subtitle = document.getElementById('weatherSubtitle');
  const summaryBox = document.getElementById('weatherSummaryBox');

  if (!weather || (Array.isArray(weather) && !weather.length) && !weather?.month_name) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';

  summaryBox.innerHTML = route.weather_summary
    ? `<div class="weather-summary-box">💬 <strong>AI про погоду:</strong> ${route.weather_summary}</div>`
    : '';

  if (weatherType === 'forecast') {
    subtitle.textContent = 'Реальний прогноз · Open-Meteo';
    renderForecast(content, Array.isArray(weather) ? weather : []);
  } else if (weatherType === 'climate_dates') {
    subtitle.textContent = 'Прогноз ще недоступний — показуємо кліматичну норму для обраного місяця';
    renderClimateCard(content, weather, route);
  } else {
    subtitle.textContent = `AI рекомендує подорожувати у ${weather.month_name || 'цей місяць'}`;
    renderClimateCard(content, weather, route);
    renderWeatherCalendar(route, weather);
  }
}

function renderForecast(container, days) {
  if (!days.length) { container.innerHTML = ''; return; }

  const temps_max = days.filter(d => d.temp_max !== null).map(d => d.temp_max);
  const temps_min = days.filter(d => d.temp_min !== null).map(d => d.temp_min);
  const total_precip = days.reduce((s, d) => s + (d.precipitation || 0), 0);
  const avg_max = temps_max.length ? (temps_max.reduce((a,b)=>a+b,0)/temps_max.length).toFixed(1) : '—';
  const avg_min = temps_min.length ? (temps_min.reduce((a,b)=>a+b,0)/temps_min.length).toFixed(1) : '—';
  const rainy_days = days.filter(d => d.precipitation > 1).length;

  const statsHtml = `
    <div class="weather-stats-row">
      <div class="weather-stat-item"><strong>${avg_max}°C</strong>Середній макс.</div>
      <div class="weather-stat-item"><strong>${avg_min}°C</strong>Середній мін.</div>
      <div class="weather-stat-item"><strong>${total_precip.toFixed(1)} мм</strong>Опади всього</div>
      <div class="weather-stat-item"><strong>${rainy_days} з ${days.length}</strong>Дощових днів</div>
    </div>
  `;

  const cardsHtml = days.map(d => {
    const dateObj = new Date(d.date + 'T12:00:00');
    const dayName = dateObj.toLocaleDateString('uk-UA', { weekday: 'short' });
    const dateStr = dateObj.toLocaleDateString('uk-UA', { day: 'numeric', month: 'short' });
    const isRainy = d.precipitation > 1;
    const isStormy = d.precipitation > 10 || (d.description && d.description.includes('Гроз'));
    const cardClass = isStormy ? 'has-storm' : isRainy ? 'has-rain' : '';

    return `
      <div class="weather-day-card ${cardClass}">
        <div class="wdc-date">${dayName}<br>${dateStr}</div>
        <div class="wdc-icon">${d.icon}</div>
        <div class="wdc-desc">${d.description}</div>
        <div class="wdc-temps">
          <span class="wdc-temp-max">${d.temp_max !== null ? Math.round(d.temp_max) + '°' : '—'}</span>
          <span class="wdc-temp-min">/ ${d.temp_min !== null ? Math.round(d.temp_min) + '°' : '—'}</span>
        </div>
        ${d.precipitation > 0.2 ? `<div class="wdc-precip">💧 ${d.precipitation} мм</div>` : ''}
        ${d.wind ? `<div class="wdc-wind">💨 ${Math.round(d.wind)} км/г</div>` : ''}
      </div>
    `;
  }).join('');

  let tip = '';
  if (rainy_days >= Math.ceil(days.length / 2)) {
    tip = '<div class="weather-tip">🌂 Більшість днів очікується дощ — візьми парасолю та водонепроникний одяг.</div>';
  } else if (parseFloat(avg_max) > 30) {
    tip = '<div class="weather-tip">🌞 Спекотно! Бери сонцезахисний крем, головний убір та більше води.</div>';
  } else if (parseFloat(avg_max) < 10) {
    tip = '<div class="weather-tip">🧥 Прохолодно — одягайся тепло, особливо ввечері.</div>';
  } else {
    tip = '<div class="weather-tip">✦ Погода сприятлива для подорожі — приємних вражень!</div>';
  }

  container.innerHTML = statsHtml + `<div class="weather-strip">${cardsHtml}</div>` + tip;
}

function renderClimateCard(container, climate, route) {
  if (!climate || Array.isArray(climate)) { container.innerHTML = ''; return; }

  container.innerHTML = `
    <div class="climate-card">
      <div class="climate-icon-big">${climate.icon}</div>
      <div>
        <div class="climate-month-name">${climate.month_name || ''}</div>
        <div class="climate-weather-desc">${climate.typical_weather}</div>
        <div class="climate-stats-grid">
          ${climate.avg_temp_max !== null ? `
            <div class="climate-stat-box">
              <span class="value">${climate.avg_temp_max}°C</span>
              <span class="label">Макс. темп.</span>
            </div>` : ''}
          ${climate.avg_temp_min !== null ? `
            <div class="climate-stat-box">
              <span class="value">${climate.avg_temp_min}°C</span>
              <span class="label">Мін. темп.</span>
            </div>` : ''}
          ${climate.avg_monthly_precip !== null ? `
            <div class="climate-stat-box">
              <span class="value">${climate.avg_monthly_precip} мм</span>
              <span class="label">Опади/місяць</span>
            </div>` : ''}
        </div>
        ${route.recommended_month_reason ? `
          <div class="climate-reason">✦ ${route.recommended_month_reason}</div>` : ''}
      </div>
    </div>
  `;
}

  // Render the hotels grid from Booking.com results; hide section when empty
  function renderHotels(hotels, route, hostelUrl) {
    const section = document.getElementById('hotelsSection');
    const grid = document.getElementById('hotelsGrid');
    if (!hotels || !hotels.length) { section.style.display = 'none'; return; }
    section.style.display = 'block';

    const metaEl = document.getElementById('hotelMeta');
    if (metaEl) {
      const prices = hotels.map(h => h.price_per_night).filter(Boolean);
      const minP = prices.length ? Math.min(...prices) : null;
      const maxP = prices.length ? Math.max(...prices) : null;
      const parts = [];
      if (route && route.hotel_type) parts.push(`<span style="color:var(--parchment)">${route.hotel_type}</span>`);
      if (minP) parts.push(`<span style="color:var(--gold)">від €${minP} до €${maxP} / ніч · Booking.com</span>`);
      if (route && route.hotel_tips) parts.push(`<span style="color:rgba(184,196,192,0.6)">${route.hotel_tips}</span>`);
      metaEl.innerHTML = parts.length
        ? `<div style="font-family:'DM Mono',monospace;font-size:0.68rem;line-height:1.7;display:flex;flex-direction:column;gap:0.2rem;">${parts.join('')}</div>`
        : '';
    }
    grid.innerHTML = hotels.map(h => {
      const stars = h.stars ? '★'.repeat(Math.min(h.stars, 5)) : '';
      const photo = h.photo
        ? `<img class="hotel-photo" src="${h.photo}" alt="${h.name}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">
           <div class="hotel-photo-placeholder" style="display:none">🏨</div>`
        : `<div class="hotel-photo-placeholder">🏨</div>`;
      const price = h.price_per_night
        ? `<div class="hotel-price">€${h.price_per_night} <span>/ ніч</span>
             ${h.total_price ? `<div class="hotel-price-total">Разом: €${h.total_price}</div>` : ''}
           </div>`
        : `<div class="hotel-price" style="color:var(--mist)">Ціна уточнюється</div>`;
      const review = h.review_score ? `<div class="hotel-review">⭐ ${h.review_score} · ${h.review_word || ''}</div>` : '';
      return `
        <div class="hotel-card">
          ${photo}
          <div class="hotel-body">
            <div class="hotel-name">${h.name}</div>
            ${stars ? `<div class="hotel-stars">${stars}</div>` : ''}
            ${review}
            ${price}
            <a class="btn-book" href="${h.booking_url}" target="_blank" rel="noopener">✦ Забронювати</a>
          </div>
        </div>`;
    }).join('');

  }

  const MODE_LABELS = { flight: 'Літак', train: 'Поїзд', bus: 'Автобус', ferry: 'Паром', car: 'Авто', multimodal: 'Всі варіанти' };
  const MODE_ICONS  = { flight: '✈️', train: '🚆', bus: '🚌', ferry: '⛴️', car: '🚗', multimodal: '🗺️' };

  // Render the transport routes section with platform booking links
  function renderTransport(t, from, to) {
    const section = document.getElementById('transportSection');
    const content = document.getElementById('transportContent');
    const routes = t.routes || [];
    if (!routes.length && !t.important_note) { section.style.display = 'none'; return; }
    section.style.display = 'block';

    const noteHtml = t.important_note
      ? `<div class="transport-important-note">⚠️ ${t.important_note}</div>` : '';

    const originHtml = t.origin
      ? `<div style="font-family:'DM Mono',monospace;font-size:0.62rem;letter-spacing:0.2em;text-transform:uppercase;color:var(--mist);margin-bottom:1rem;">ВІДПРАВЛЕННЯ: ${t.origin}</div>` : '';

    const lt = t.local_transport || {};
    const localTransportHtml = (lt.single_ride || lt.day_pass || lt.note) ? `
      <div style="display:flex;flex-wrap:wrap;gap:0.6rem;align-items:flex-start;margin-bottom:1.5rem;padding:1rem 1.2rem;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:10px;">
        <div style="font-family:'DM Mono',monospace;font-size:0.6rem;letter-spacing:0.2em;text-transform:uppercase;color:var(--mist);width:100%;margin-bottom:0.4rem;">Громадський транспорт у місті</div>
        ${lt.day_pass && lt.day_pass !== 'null' ? `<div style="display:flex;flex-direction:column;gap:0.15rem;"><span style="font-size:0.68rem;color:var(--mist);">Денний квиток</span><span style="font-size:1rem;font-weight:700;color:var(--gold);">${lt.day_pass}</span></div>` : ''}
        ${lt.single_ride ? `<div style="display:flex;flex-direction:column;gap:0.15rem;${lt.day_pass && lt.day_pass !== 'null' ? 'margin-left:1.2rem;padding-left:1.2rem;border-left:1px solid rgba(255,255,255,0.08);' : ''}"><span style="font-size:0.68rem;color:var(--mist);">Разовий квиток</span><span style="font-size:1rem;font-weight:700;color:var(--parchment);">${lt.single_ride}</span></div>` : ''}
        ${lt.note ? `<div style="width:100%;margin-top:0.5rem;font-size:0.8rem;color:var(--mist);line-height:1.5;">${lt.note}</div>` : ''}
      </div>` : '';

    const routesHtml = routes.map(route => {
      const legs = route.legs || [];
      const legsHtml = legs.map(leg => {
        const modeIcon = MODE_ICONS[leg.mode] || '🚀';
        const modeLabel = MODE_LABELS[leg.mode] || leg.mode || '';
        const linksHtml = (leg.links || []).length
          ? `<div class="transport-btns">${(leg.links).map(l =>
              `<a class="transport-btn" href="${l.url}" target="_blank" rel="noopener" style="background:${l.bg||'#555'}">${l.name}</a>`
            ).join('')}</div>` : '';
        return `
          <div class="transport-leg">
            <div class="transport-leg-top">
              <span class="transport-leg-step">Крок ${leg.step}</span>
              <div class="transport-leg-route">
                <span>${leg.from}</span>
                <span class="transport-leg-arrow">→</span>
                <span>${leg.to}</span>
              </div>
              <span class="transport-leg-mode">${modeIcon} ${modeLabel}</span>
              ${leg.duration ? `<span class="transport-leg-duration">${leg.duration}</span>` : ''}
            </div>
            ${leg.note ? `<div class="transport-leg-note">${leg.note}</div>` : ''}
            ${linksHtml}
          </div>`;
      }).join('');

      return `
        <div class="transport-route-card${route.recommended ? ' is-recommended' : ''}">
          <div class="transport-route-header">
            <span class="transport-route-label">${route.label || ''}</span>
            ${route.recommended ? `<span class="transport-route-rec-badge">★ Рекомендовано</span>` : ''}
          </div>
          <div class="transport-route-meta">
            ${route.total_duration ? `<span>⏱ ${route.total_duration}</span>` : ''}
            ${route.estimated_cost ? `<span>💶 ${route.estimated_cost}</span>` : ''}
          </div>
          ${route.summary ? `<div class="transport-route-summary">${route.summary}</div>` : ''}
          <div class="transport-legs">${legsHtml}</div>
        </div>`;
    }).join('');

    let ticketHtml = '';
    if (from && to) {
      const fromSlug = from.trim().replace(/[\s,]+/g, '-');
      const toSlug   = to.trim().replace(/[\s,]+/g, '-');
      const fromEnc  = encodeURIComponent(from.trim());
      const toEnc    = encodeURIComponent(to.trim());
      const tLinks = [
        { name: 'Rome2Rio',       color: '#e05033', url: `https://www.rome2rio.com/s/${fromSlug}/${toSlug}` },
        { name: 'Google Flights', color: '#4285F4', url: `https://www.google.com/travel/flights?q=flights+from+${fromEnc}+to+${toEnc}` },
        { name: 'Kiwi.com',       color: '#00b2a1', url: `https://www.kiwi.com/en/search/results/${fromSlug}/${toSlug}` },
        { name: 'Omio',           color: '#55af2e', url: `https://www.omio.com/` },
      ];
      ticketHtml = `
        <div class="ticket-search" style="margin-top:1.8rem;">
          <div class="ticket-search-title">Пошук квитків онлайн</div>
          <div class="ticket-search-route">
            <span>${from}</span><span class="arrow">→</span><span>${to}</span>
          </div>
          <div class="ticket-btns">
            ${tLinks.map(l => `<a class="ticket-btn" href="${l.url}" target="_blank" rel="noopener" style="background:${l.color}">${l.name}</a>`).join('')}
          </div>
        </div>`;
    }

    content.innerHTML = noteHtml + originHtml + localTransportHtml + `<div class="transport-routes">${routesHtml}</div>` + ticketHtml;
  }

  // Increment or decrement the adult traveller count (min 1)
  function changePeople(delta) {
    const el = document.getElementById('num_people');
    const next = Math.max(1, Math.min(10, (parseInt(el.value) || 1) + delta));
    el.value = next;
    document.getElementById('peopleCount').textContent = next;
    document.getElementById('peopleDecBtn').disabled = next <= 1;
    document.getElementById('peopleIncBtn').disabled = next >= 10;
  }
  // Increment or decrement the children count (min 0)
  function changeChildren(delta) {
    const el = document.getElementById('num_children');
    const next = Math.max(0, Math.min(8, (parseInt(el.value) || 0) + delta));
    el.value = next;
    document.getElementById('childrenCount').textContent = next;
    document.getElementById('childrenDecBtn').disabled = next <= 0;
    document.getElementById('childrenIncBtn').disabled = next >= 8;
  }

  document.querySelectorAll('.interest-tag').forEach(tag => {
    tag.addEventListener('click', () => { tag.classList.toggle('active'); });
  });

  // Collect the values of all active interest tags from the search form only
  function getInterests() {
    return [...document.querySelectorAll('.form-panel .interest-tag.active')].map(t => t.dataset.value);
  }

  // Set min date for date inputs
  const today = new Date().toISOString().split('T')[0];
  document.getElementById('checkin_date').min = today;
  document.getElementById('checkout_date').min = today;
  document.getElementById('checkin_date').addEventListener('change', function() {
    document.getElementById('checkout_date').min = this.value;
  });

  // Render recommended similar-route cards below the search form
  function renderQuickRoutes(routes) {
    const el = document.getElementById('quickRoutesList');
    if (!el) return;
    if (!routes || !routes.length) return;
    el.innerHTML = routes.map(r => `
      <div class="route-card-mini">
        <div class="route-card-mini-header">
          <div><h4>${r.title}</h4><div class="dest">${r.destination}</div></div>
        </div>
        <p style="font-size:0.85rem; color:var(--mist); margin:0.4rem 0;">${r.description}</p>
        <div class="route-meta">
          <span class="route-meta-item">${r.duration}</span>
        </div>
      </div>`).join('');
  }

  let _pendingSuggestion = null;

  async function generateRoute() {
    if (weatherMode === 'prefs') {
      await _suggestDatesStep();
      return;
    }
    await _doFullGeneration();
  }

  // Load and display the pre-built demo route without authentication
  async function loadDemoRoute() {
    _isDemo = true;
    const loading = document.getElementById('loadingOverlay');
    loading.classList.add('visible');
    try {
      const res  = await fetch('/api/demo-route');
      const data = await res.json();
      if (data.success) {
        renderResult(data.ai_route, data.similar_routes, data.hotels,
                     data.weather, data.weather_type, data.transport, data.hostel_url);
      } else {
        alert('Помилка завантаження демо: ' + (data.error || 'невідома помилка'));
        _isDemo = false;
      }
    } catch (e) {
      alert('Мережева помилка: ' + e.message);
      _isDemo = false;
    } finally {
      loading.classList.remove('visible');
    }
  }

  async function _suggestDatesStep() {
    const btn = document.getElementById('generateBtn');
    const loading = document.getElementById('loadingOverlay');
    btn.disabled = true;
    loading.classList.add('visible');

    calStartDate = null;
    calEndDate = null;
    _pendingSuggestion = null;

    try {
      const res = await fetch('/api/suggest-dates', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          destination:    document.getElementById('destination').value,
          weather_pref:   document.getElementById('weather_pref').value,
          departure_city: document.getElementById('departure_city').value,
          duration:       document.getElementById('duration').value,
        })
      });
      const data = await res.json();
      if (!data.success) { alert('Помилка: ' + data.error); return; }

      _pendingSuggestion = data;
      _showDatePickerStep(data);
    } catch(e) {
      alert('Мережева помилка: ' + e.message);
    } finally {
      btn.disabled = false;
      loading.classList.remove('visible');
    }
  }

  function _showDatePickerStep(s) {
    const now = new Date();
    const year = now.getFullYear() + (s.recommended_month <= now.getMonth() + 1 ? 1 : 0);

    document.getElementById('dpSuggestionInfo').innerHTML = `
      <div style="font-family:'DM Mono',monospace;font-size:0.65rem;letter-spacing:0.2em;text-transform:uppercase;color:var(--terracotta);margin-bottom:0.6rem;">AI рекомендує</div>
      <div style="font-size:1.3rem;font-weight:700;margin-bottom:0.3rem;">${s.city_ua || s.city}${s.country_ua ? ', ' + s.country_ua : ''}</div>
      <div style="font-size:0.9rem;color:var(--mist);margin-bottom:0.8rem;">Найкращий час: <span style="color:var(--gold)">${s.month_name}</span></div>
      ${s.typical_weather ? `<div style="font-size:0.85rem;color:var(--mist);font-style:italic;">${s.typical_weather}</div>` : ''}
    `;

    const calWrap = document.getElementById('dpCalendarWrap');
    calWrap.innerHTML = `<div style="font-size:0.85rem;color:var(--mist);font-style:italic;padding:1rem 0;">⏳ Завантажуємо погоду по днях...</div>`;

    document.getElementById('dpSelectedDates').innerHTML = '';
    document.getElementById('dpGenerateBtn').style.display = 'none';
    document.getElementById('datePickerStep').style.display = 'block';

    setTimeout(() => document.getElementById('datePickerStep').scrollIntoView({behavior:'smooth', block:'start'}), 100);

    fetch('/api/weather-month', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ lat: s.lat, lng: s.lng, month: s.recommended_month, year })
    })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        _buildDpCalendar(calWrap, data.days, s.recommended_month, year);
      } else {
        calWrap.innerHTML = `<div style="color:var(--mist);font-size:0.85rem;">Не вдалося завантажити погоду. Оберіть дати вручну вище.</div>`;
      }
    })
    .catch(() => {
      calWrap.innerHTML = `<div style="color:var(--mist);font-size:0.85rem;">Помилка завантаження. Оберіть дати вручну вище.</div>`;
    });
  }

  function _buildDpCalendar(wrap, days, month, year) {
    const WEEKDAYS = ['Пн','Вт','Ср','Чт','Пт','Сб','Нд'];
    const MONTHS_UA = ['Січень','Лютий','Березень','Квітень','Травень','Червень',
                       'Липень','Серпень','Вересень','Жовтень','Листопад','Грудень'];
    const dayMap = {};
    days.forEach(d => { dayMap[parseInt(d.date.split('-')[2])] = d; });

    const firstDay = new Date(year, month - 1, 1).getDay();
    const offset = firstDay === 0 ? 6 : firstDay - 1;
    const daysInMonth = new Date(year, month, 0).getDate();
    const today = new Date().toISOString().split('T')[0];

    let wdHtml = WEEKDAYS.map(d => `<div class="wcal-weekday">${d}</div>`).join('');
    let cellsHtml = '';
    for (let i = 0; i < offset; i++) cellsHtml += `<div class="wcal-cell empty"></div>`;
    for (let d = 1; d <= daysInMonth; d++) {
      const info = dayMap[d];
      const dateStr = `${year}-${String(month).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
      const isPast = dateStr < today;
      const isRain = info && info.precipitation > 2;
      const isStorm = info && info.precipitation > 10;
      const rainClass = isStorm ? 'storm-day' : isRain ? 'rain-day' : '';
      const pastStyle = isPast ? 'opacity:0.3;pointer-events:none;' : '';
      cellsHtml += `
        <div class="wcal-cell ${rainClass}" data-date="${dateStr}" style="${pastStyle}" onclick="dpSelectDate('${dateStr}')">
          <div class="wcal-day-num">${d}</div>
          <div class="wcal-icon">${info ? info.icon : '❓'}</div>
          <div class="wcal-temp">
            ${info?.temp_max != null ? Math.round(info.temp_max)+'°' : '—'}
            <span class="mn">/${info?.temp_min != null ? Math.round(info.temp_min)+'°' : '—'}</span>
          </div>
          ${info?.precipitation > 0.5 ? `<div class="wcal-precip">💧${info.precipitation}</div>` : ''}
        </div>`;
    }

    wrap.innerHTML = `
      <div class="wcal-header" style="margin-bottom:0.8rem;">
        <div class="wcal-title">📅 ${MONTHS_UA[month-1]} ${year}</div>
        <div class="wcal-hint">Клікніть початкову і кінцеву дату</div>
      </div>
      <div class="wcal-weekdays">${wdHtml}</div>
      <div class="wcal-grid" id="dpCalGrid">${cellsHtml}</div>
    `;
  }

  function dpSelectDate(dateStr) {
    if (!calStartDate || (calStartDate && calEndDate)) {
      calStartDate = dateStr; calEndDate = null;
    } else {
      if (dateStr < calStartDate) { calEndDate = calStartDate; calStartDate = dateStr; }
      else calEndDate = dateStr;
    }
    _updateDpHighlight();
  }

  function _updateDpHighlight() {
    document.querySelectorAll('#dpCalGrid .wcal-cell:not(.empty)').forEach(cell => {
      const d = cell.dataset.date;
      cell.classList.remove('selected-start', 'selected-end', 'in-range');
      if (d === calStartDate) cell.classList.add('selected-start');
      else if (d === calEndDate) cell.classList.add('selected-end');
      else if (calStartDate && calEndDate && d > calStartDate && d < calEndDate) cell.classList.add('in-range');
    });

    const container = document.getElementById('dpSelectedDates');
    const genBtn = document.getElementById('dpGenerateBtn');
    if (calStartDate && calEndDate) {
      const nights = Math.round((new Date(calEndDate) - new Date(calStartDate)) / 86400000);
      const fmt = d => new Date(d + 'T12:00:00').toLocaleDateString('uk-UA', {day:'numeric', month:'long'});
      container.innerHTML = `
        <div style="margin-top:0.8rem;padding:0.8rem 1.2rem;background:rgba(80,104,240,0.08);border:1px solid rgba(80,104,240,0.25);border-radius:8px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.5rem;">
          <span style="font-family:'DM Mono',monospace;font-size:0.75rem;color:var(--parchment);">
            ${fmt(calStartDate)} — ${fmt(calEndDate)} · ${nights} ніч${nights===1?'':'і'}
          </span>
        </div>`;
      genBtn.style.display = 'block';
    } else if (calStartDate) {
      const fmt = d => new Date(d + 'T12:00:00').toLocaleDateString('uk-UA', {day:'numeric', month:'long'});
      container.innerHTML = `<div style="margin-top:0.5rem;font-size:0.82rem;color:var(--mist);">📍 ${fmt(calStartDate)} — оберіть дату виїзду</div>`;
      genBtn.style.display = 'none';
    } else {
      container.innerHTML = '';
      genBtn.style.display = 'none';
    }
  }

  async function _doFullGeneration() {
    _isDemo = false;
    // If coming from prefs+calendar, fill in the dates first
    if (weatherMode === 'prefs' && calStartDate && calEndDate) {
      document.getElementById('checkin_date').value  = calStartDate;
      document.getElementById('checkout_date').value = calEndDate;
      // fill destination from suggestion if user left it blank
      if (_pendingSuggestion && !document.getElementById('destination').value.trim()) {
        document.getElementById('destination').value = _pendingSuggestion.city || '';
      }
    }

    const btn = document.getElementById('generateBtn');
    const dpBtn = document.getElementById('dpGenerateBtn');
    const loading = document.getElementById('loadingOverlay');
    if (btn) btn.disabled = true;
    if (dpBtn) dpBtn.disabled = true;
    loading.classList.add('visible');

    _departureCity = (document.getElementById('departure_city').value || '').trim();

    const payload = {
      budget: 0,
      budget_level: budgetLevel,
      interests: getInterests(),
      num_people: parseInt(document.getElementById('num_people').value) || 1,
      num_children: parseInt(document.getElementById('num_children').value) || 0,
      departure_city: _departureCity,
      destination: document.getElementById('destination').value,
      duration: document.getElementById('duration').value,
      extra_notes: document.getElementById('extra_notes').value,
      checkin_date:  document.getElementById('checkin_date').value,
      checkout_date: document.getElementById('checkout_date').value,
      weather_pref:  '',
    };

    try {
      const res = await fetch('/api/generate-route', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let data = null;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const text = line.slice(6).trim();
          if (text === '[DONE]') continue;
          const parsed = JSON.parse(text);
          if (parsed.status === 'generating') continue;
          data = parsed;
        }
      }
      if (data && data.success) {
        document.getElementById('datePickerStep').style.display = 'none';
        renderResult(data.ai_route, data.similar_routes, data.hotels, data.weather, data.weather_type, data.transport, data.hostel_url);
      } else if (data) {
        alert('Помилка: ' + data.error);
      }
    } catch(e) {
      alert('Мережева помилка: ' + e.message);
    } finally {
      if (btn) btn.disabled = false;
      if (dpBtn) dpBtn.disabled = false;
      loading.classList.remove('visible');
    }
  }

  // Return the backend URL for a restaurant photo, preferring maps_query over cuisine
  function getRestaurantPhoto(mapsQuery, cuisine, photoQuery) {
    if (!mapsQuery && !cuisine && !photoQuery) return null;
    return '/api/restaurant-photo'
      + '?q='       + encodeURIComponent(mapsQuery  || '')
      + '&cuisine=' + encodeURIComponent(cuisine    || '')
      + '&photo_q=' + encodeURIComponent(photoQuery || '');
  }

  // Normalise a raw price string (e.g. "€12" or "12 EUR") to a € symbol string
  function normPrice(raw) {
    if (!raw) return null;
    const s = String(raw).trim();
    if (/^€+$/.test(s)) return s;
    if (/budget|бюджет|cheap|дешев/i.test(s)) return '€';
    if (/moderate|середн|mid|normal/i.test(s)) return '€€';
    if (/expensive|дорог|upscale|fine|luxury|преміум/i.test(s)) return '€€€';
    return s;
  }

  const _rcGradients = [
    'linear-gradient(135deg,rgba(196,92,58,0.28) 0%,rgba(196,160,82,0.12) 100%)',
    'linear-gradient(135deg,rgba(90,122,92,0.28) 0%,rgba(196,160,82,0.1) 100%)',
    'linear-gradient(135deg,rgba(100,149,237,0.22) 0%,rgba(90,122,92,0.1) 100%)',
  ];

  // Build the restaurant cards HTML block for a day panel
  function _buildRestaurantsHtml(restaurants) {
    if (!restaurants || !restaurants.length) return '';
    return `
      <div class="restaurants-section">
        <div class="rest-label">✦ Де поїсти</div>
        <div class="restaurant-cards">
          ${restaurants.map((r, ri) => {
            const mapsUrl = 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(r.maps_query || r.name);
            const photoUrl = getRestaurantPhoto(r.maps_query, r.cuisine, r.photo_query);
            const price = normPrice(r.price || r.price_level);
            const emojiB = r.cuisine_emoji || '🍽';
            const grad = _rcGradients[ri % _rcGradients.length];
            const photoHtml = photoUrl
              ? `<div class="rc-photo-wrap">
                   <img class="rc-photo" src="${photoUrl}" alt="${r.cuisine}" loading="lazy"
                        onerror="this.style.display='none';this.nextElementSibling.style.display='flex';this.nextElementSibling.nextElementSibling.style.display='none'">
                   <div class="rc-photo-placeholder" style="display:none;background:${grad}"><span class="rc-photo-emoji">${emojiB}</span></div>
                   <span class="rc-emoji-badge">${emojiB}</span>
                 </div>`
              : `<div class="rc-photo-placeholder" style="background:${grad}"><span class="rc-photo-emoji">${emojiB}</span></div>`;
            return `
              <div class="restaurant-card">
                ${photoHtml}
                <div class="rc-body">
                  <div class="rc-name">${r.name}</div>
                  <div class="rc-meta">
                    <span class="rc-cuisine">${r.cuisine || ''}</span>
                    ${price ? `<span class="rc-price">${price}</span>` : ''}
                    ${r.rating ? `<span class="rc-rating">⭐ ${r.rating}</span>` : ''}
                  </div>
                  ${r.description ? `<div class="rc-desc">${r.description}</div>` : ''}
                  ${r.review ? `<div class="rc-review">"${r.review}"</div>` : ''}
                  <a class="rc-maps-link" href="${mapsUrl}" target="_blank" rel="noopener">🗺 Google Maps →</a>
                </div>
              </div>`;
          }).join('')}
        </div>
      </div>`;
  }

  // Master render function: populates the results page with all AI-generated sections
  function renderResult(route, similar, hotels, weather, weatherType, transport, hostelUrl) {
    document.getElementById('resultTitle').textContent = route.title || '—';
    document.getElementById('resultTagline').textContent = route.tagline || '';

    document.getElementById('metaBadges').innerHTML = `
      <span class="badge budget">💶 ${route.estimated_budget || '?'}</span>
      <span class="badge duration">📅 ${route.duration || '?'}</span>
      <span class="badge season">🌤 ${route.best_season || '?'}</span>
      <span class="badge difficulty">⚡ ${route.difficulty || '?'}</span>
    `;

    initCarousel(route.days || []);
    renderWeather(weather, weatherType, route);
    renderHotels(hotels || [], route, hostelUrl);
    renderTransport(transport || {}, _departureCity, route.destination_city || route.destination || '');

    const tips = route.practical_tips || [];
    const gems = route.hidden_gems || [];
    document.getElementById('extraInfo').innerHTML = `
      <div class="info-block">
        <h3>Практичні поради</h3>
        <ul>${tips.map(t => `<li>${t}</li>`).join('')}</ul>
      </div>
      <div class="info-block">
        <h3>Приховані скарби</h3>
        <ul>${gems.map(g => `<li>${g}</li>`).join('')}</ul>
      </div>`;

    const bd = route.budget_detail || {};
    const bdCategories = [
      { key: 'accommodation',    label: 'Проживання',        color: '#5068f0' },
      { key: 'transport',        label: 'Квитки туди-назад', color: '#f0b746' },
      { key: 'food',             label: 'Харчування',        color: '#2ec98a' },
      { key: 'activities',       label: 'Вхідні квитки',     color: '#f87878' },
      { key: 'local_transport',  label: 'Транспорт у місті', color: '#7880a8' },
      { key: 'misc',             label: 'Дрібні витрати',    color: '#c4a052' },
    ];
    const totalMin = bd.total_min || 0;
    const totalMax = bd.total_max || 0;
    const maxSubtotal = Math.max(...bdCategories.map(c => (bd[c.key] || {}).subtotal || 0), 1);
    const rowsHtml = bdCategories.map(({ key, label, color }) => {
      const cat = bd[key];
      if (!cat) return '';
      const sub = cat.subtotal || 0;
      const barPct = Math.round((sub / maxSubtotal) * 100);
      let calcStr = '';
      if (key === 'accommodation' && cat.price_per_night && cat.nights)
        calcStr = `${cat.price_per_night} EUR × ${cat.nights} ночей`;
      else if (key === 'food' && cat.per_day && cat.days)
        calcStr = `${cat.per_day} EUR/день × ${cat.days} днів`;
      return `
        <div class="budget-row">
          <div class="budget-row-left">
            <span class="budget-row-label">${label}</span>
            ${calcStr ? `<span class="budget-row-calc">${calcStr}</span>` : ''}
            ${cat.note ? `<span class="budget-row-note">${cat.note}</span>` : ''}
          </div>
          <span class="budget-row-amount">~${sub} EUR</span>
          <div class="budget-row-bar"><div class="budget-row-bar-fill" style="width:${barPct}%;background:${color}"></div></div>
        </div>`;
    }).join('');
    const totalHtml = (totalMin || totalMax) ? `
      <div class="budget-total-row">
        <span class="budget-total-label">Загальний бюджет</span>
        <span class="budget-total-amount">${totalMin}–${totalMax} EUR</span>
      </div>` : '';
    document.getElementById('budgetChart').innerHTML = `
      <h3>Орієнтовний бюджет</h3>
      <div class="budget-rows">${rowsHtml}</div>
      ${totalHtml}`;

    const simEl = document.getElementById('similarSection');
    if (similar && similar.length && simEl) {
      const stars = n => '★'.repeat(Math.round(n)) + '☆'.repeat(5 - Math.round(n));
      const budgeLabel = {'budget':'Бюджетний','mid':'Комфорт','premium':'Преміум'};
      simEl.innerHTML = `
        <h3>Схожі маршрути від спільноти</h3>
        <div class="similar-grid">
          ${similar.map(r => `
            <div class="similar-card" onclick="openRecommendedRoute(${r.id})" title="Переглянути маршрут">
              <div class="sim-badge">${budgeLabel[r.budget_level] || r.budget_level}</div>
              <h4>${r.title}</h4>
              <p>${r.destination} · ${r.duration}</p>
              <div class="sim-rating">
                <span class="sim-stars">${stars(r.avg_rating)}</span>
                <span class="sim-score">${r.avg_rating.toFixed(1)}</span>
                <span class="sim-reviews">(${r.review_count} ${r.review_count === 1 ? 'відгук' : r.review_count < 5 ? 'відгуки' : 'відгуків'})</span>
              </div>
              <div class="sim-open-hint">Переглянути →</div>
            </div>`).join('')}
        </div>`;
    } else if (simEl) {
      simEl.innerHTML = '';
    }

    const saveBtn = document.getElementById('saveRouteBtn');
    if (saveBtn) {
      saveBtn.style.display = (_currentUser && !_isDemo) ? 'inline-flex' : 'none';
      saveBtn.textContent = '💾 Зберегти маршрут';
      saveBtn.disabled = false;
      saveBtn.onclick = () => saveCurrentRoute(route, getInterests(), budgetLevel);
    }

    const demoBanner = document.getElementById('demoBanner');
    if (demoBanner) demoBanner.style.display = _isDemo ? 'flex' : 'none';

    document.getElementById('resultSection').classList.add('visible');
    showPage('results');
  }

let calStartDate = null;
let calEndDate = null;
let calRouteData = null;

function renderWeatherCalendar(route, weather) {
  const section = document.getElementById('weatherSection');
  const existing = document.getElementById('weatherCalendar');
  if (existing) existing.remove();

  if (!route || !weather) return;

  const month = route.recommended_month;
  const now = new Date();
  const year = now.getFullYear() + (month <= now.getMonth() + 1 ? 1 : 0);
  const lat = route.days?.[0]?.lat;
  const lng = route.days?.[0]?.lng;

  if (lat == null || lng == null || !month) return;

  calRouteData = { route, lat, lng, month, year };
  calStartDate = null;
  calEndDate = null;

  const wrap = document.createElement('div');
  wrap.id = 'weatherCalendar';
  wrap.className = 'weather-calendar-wrap';
  wrap.innerHTML = `
    <div class="wcal-header">
      <div class="wcal-title">📅 Оберіть дати подорожі</div>
      <div class="wcal-hint">Клікніть на початкову та кінцеву дату</div>
    </div>
    <div class="wcal-loading">⏳ Завантажуємо погоду по днях...</div>
  `;
  section.appendChild(wrap);

  fetch('/api/weather-month', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat, lng, month, year })
  })
  .then(r => r.json())
  .then(data => {
    if (data.success) {
      buildCalendar(wrap, data.days, month, year, route);
    } else {
      wrap.querySelector('.wcal-loading').textContent = 'Не вдалося завантажити погоду';
    }
  })
  .catch(() => {
    wrap.querySelector('.wcal-loading').textContent = 'Помилка завантаження погоди';
  });
}

function buildCalendar(wrap, days, month, year, route) {
  const WEEKDAYS = ['Пн','Вт','Ср','Чт','Пт','Сб','Нд'];
  const MONTHS_UA = ['Січень','Лютий','Березень','Квітень','Травень','Червень',
                     'Липень','Серпень','Вересень','Жовтень','Листопад','Грудень'];

  const dayMap = {};
  days.forEach(d => {
    const num = parseInt(d.date.split('-')[2]);
    dayMap[num] = d;
  });

  const firstDay = new Date(year, month - 1, 1).getDay();
  const offset = firstDay === 0 ? 6 : firstDay - 1;
  const daysInMonth = new Date(year, month, 0).getDate();

  let gridHtml = WEEKDAYS.map(d => `<div class="wcal-weekday">${d}</div>`).join('');
  let cellsHtml = '';

  for (let i = 0; i < offset; i++) {
    cellsHtml += `<div class="wcal-cell empty"></div>`;
  }

  for (let d = 1; d <= daysInMonth; d++) {
    const info = dayMap[d];
    const dateStr = `${year}-${String(month).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const isRain = info && info.precipitation > 2;
    const isStorm = info && info.precipitation > 10;
    const rainClass = isStorm ? 'storm-day' : isRain ? 'rain-day' : '';

    cellsHtml += `
      <div class="wcal-cell ${rainClass}" data-date="${dateStr}" onclick="calSelectDate('${dateStr}')">
        <div class="wcal-day-num">${d}</div>
        <div class="wcal-icon">${info ? info.icon : '❓'}</div>
        <div class="wcal-temp">
          ${info?.temp_max !== null ? Math.round(info.temp_max) + '°' : '—'}
          <span class="mn">/${info?.temp_min !== null ? Math.round(info.temp_min) + '°' : '—'}</span>
        </div>
        ${info?.precipitation > 0.5 ? `<div class="wcal-precip">💧${info.precipitation}</div>` : ''}
      </div>`;
  }

  wrap.innerHTML = `
    <div class="wcal-header">
      <div class="wcal-title">📅 ${MONTHS_UA[month-1]} ${year}</div>
      <div class="wcal-hint">Клікніть початкову і кінцеву дату</div>
    </div>
    <div class="wcal-weekdays">${gridHtml}</div>
    <div class="wcal-grid" id="wcalGrid">${cellsHtml}</div>
    <div id="wcalSelectedDates"></div>
  `;
}

function calSelectDate(dateStr) {
  if (!calStartDate || (calStartDate && calEndDate)) {
    calStartDate = dateStr;
    calEndDate = null;
  } else {
    if (dateStr < calStartDate) {
      calEndDate = calStartDate;
      calStartDate = dateStr;
    } else {
      calEndDate = dateStr;
    }
  }
  updateCalendarHighlight();
}

function updateCalendarHighlight() {
  const cells = document.querySelectorAll('.wcal-cell:not(.empty)');
  cells.forEach(cell => {
    const d = cell.dataset.date;
    cell.classList.remove('selected-start', 'selected-end', 'in-range');
    if (d === calStartDate) cell.classList.add('selected-start');
    else if (d === calEndDate) cell.classList.add('selected-end');
    else if (calStartDate && calEndDate && d > calStartDate && d < calEndDate) {
      cell.classList.add('in-range');
    }
  });

  const container = document.getElementById('wcalSelectedDates');
  if (calStartDate && calEndDate) {
    const nights = Math.round((new Date(calEndDate) - new Date(calStartDate)) / 86400000);
    const fmt = d => new Date(d + 'T12:00:00').toLocaleDateString('uk-UA', { day: 'numeric', month: 'long' });
    container.innerHTML = `
      <div class="wcal-selected-dates">
        <div class="wcal-selected-text">
          ✦ ${fmt(calStartDate)} — ${fmt(calEndDate)} · ${nights} ніч${nights === 1 ? '' : nights < 5 ? 'і' : 'ей'}
        </div>
        <button class="btn-use-dates" onclick="applyCalendarDates()">
          Використати ці дати →
        </button>
      </div>
    `;
  } else if (calStartDate) {
    const fmt = d => new Date(d + 'T12:00:00').toLocaleDateString('uk-UA', { day: 'numeric', month: 'long' });
    container.innerHTML = `
      <div class="wcal-selected-dates">
        <div class="wcal-selected-text">📍 ${fmt(calStartDate)} — оберіть дату виїзду</div>
      </div>
    `;
  } else {
    container.innerHTML = '';
  }
}

function applyCalendarDates() {
  if (!calStartDate || !calEndDate) return;

  setWeatherMode('dates');

  const checkinInput = document.getElementById('checkin_date');
  const checkoutInput = document.getElementById('checkout_date');

  checkinInput.value = calStartDate;
  checkoutInput.min = calStartDate;
  checkoutInput.value = calEndDate;

  // If this calendar is on the results page, re-generate with the new dates
  if (document.getElementById('pageResults').style.display !== 'none') {
    _doFullGeneration();
  } else {
    showPage('search');
    [checkinInput, checkoutInput].forEach(inp => {
      inp.style.borderColor = 'var(--terracotta)';
      inp.style.background = 'rgba(80,104,240,0.1)';
      setTimeout(() => { inp.style.borderColor = ''; inp.style.background = ''; }, 2000);
    });
  }
}

let _currentUser = null;
let _resetToken  = null;
let _isDemo      = false;

// Show the post-registration onboarding interest survey
function showOnboarding(name) {
  document.getElementById('onboardingGreeting').textContent = `Ласкаво просимо, ${name}!`;
  document.getElementById('welcomeAuthBox').style.display = 'none';
  document.querySelector('.demo-divider').style.display = 'none';
  document.querySelector('.btn-demo').style.display = 'none';
  document.getElementById('welcomeOnboarding').style.display = 'flex';
  document.querySelectorAll('#onboardingInterestGrid .interest-tag').forEach(t => t.classList.remove('active'));
  showPage('welcome');
}

// Toggle a tag in the onboarding interest grid
function toggleOnboardingTag(tag) {
  tag.classList.toggle('active');
}

// Save selected interests and fetch personalized recommendations
async function saveOnboardingInterests() {
  const btn = document.getElementById('onboardingSaveBtn');
  if (btn) btn.disabled = true;
  const interests = Array.from(document.querySelectorAll('#onboardingInterestGrid .interest-tag.active'))
    .map(t => t.dataset.value);
  try {
    const res  = await fetch('/api/auth/save-interests', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ interests }),
    });
    const data = await res.json();
    _closeOnboarding();
    if (interests.length) _applyUserInterests(interests);
    if (data.recommendations && data.recommendations.length) {
      _showRecommendedRoutes(data.recommendations);
    }
    showPage('search');
  } catch (e) {
    _closeOnboarding();
    showPage('search');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Skip onboarding and go directly to search
function skipOnboarding() {
  _closeOnboarding();
  showPage('search');
}

function _closeOnboarding() {
  document.getElementById('welcomeOnboarding').style.display = 'none';
  document.getElementById('welcomeAuthBox').style.display = 'block';
  document.querySelector('.demo-divider').style.display = 'flex';
  document.querySelector('.btn-demo').style.display = 'block';
}

// Pre-select matching interest tags in the search form
function _applyUserInterests(interests) {
  const vals = new Set(interests.map(i => (i || '').toLowerCase()));
  document.querySelectorAll('.form-panel .interest-tag').forEach(tag => {
    tag.classList.toggle('active', vals.has((tag.dataset.value || '').toLowerCase()));
  });
}

// Render personalized recommendation cards in the quick-routes panel
function _showRecommendedRoutes(routes) {
  const panel = document.getElementById('quickRoutesPanel');
  const titleEl = document.getElementById('quickRoutesTitle');
  const listEl  = document.getElementById('quickRoutesList');
  if (!panel || !listEl) return;
  if (titleEl) titleEl.textContent = 'Маршрути для вас';
  const stars = n => '★'.repeat(Math.round(n)) + '☆'.repeat(5 - Math.round(n));
  const budgeLabel = { budget: 'Бюджетний', mid: 'Комфорт', premium: 'Преміум' };
  listEl.innerHTML = routes.map(r => `
    <div class="rec-route-card" onclick="openRecommendedRoute(${r.id})" title="Переглянути маршрут">
      <div class="rec-route-title">${r.title}</div>
      <div class="rec-route-dest">${r.destination || ''}</div>
      <div class="rec-route-meta">
        <span class="rec-route-dur">${r.duration || ''}</span>
        <span class="rec-route-budget">${budgeLabel[r.budget_level] || r.budget_level || ''}</span>
        ${r.avg_rating ? `<span class="rec-route-rating">${stars(r.avg_rating)} ${r.avg_rating}</span>` : ''}
      </div>
    </div>`).join('');
  panel.style.display = 'block';
}

async function _checkAuth() {
  const params = new URLSearchParams(window.location.search);
  const token  = params.get('reset_token');
  if (token) {
    _resetToken = token;
    history.replaceState({}, '', window.location.pathname);
    showPage('welcome');
    switchWelcomeTab('reset');
    return;
  }
  const res = await fetch('/api/auth/me');
  const data = await res.json();
  _currentUser = data.user;
  _updateHeaderAuth();
  if (_currentUser) {
    if (_currentUser.interests && _currentUser.interests.length) {
      _applyUserInterests(_currentUser.interests);
    }
    showPage('search');
  } else {
    showPage('welcome');
  }
}

function _updateHeaderAuth() {
  const el = document.getElementById('headerAuth');
  if (_currentUser) {
    const initial = (_currentUser.name || '?')[0].toUpperCase();
    el.innerHTML = `
      <div class="profile-menu" id="profileMenu">
        <button class="profile-avatar" onclick="toggleProfileMenu(event)" title="${_currentUser.name}">${initial}</button>
        <div class="profile-dropdown" id="profileDropdown">
          <div class="profile-dropdown-name">${_currentUser.name}</div>
          <button class="profile-dropdown-btn" onclick="openSavedPanel();closeProfileMenu()">Збережені маршрути</button>
          <button class="profile-dropdown-btn danger" onclick="doLogout()">Вийти</button>
        </div>
      </div>`;
    const saveBtn = document.getElementById('saveRouteBtn');
    if (saveBtn) saveBtn.style.display = 'inline-flex';
  } else {
    el.innerHTML = `
      <button class="btn-auth" onclick="openAuth('login')">Увійти</button>
      <button class="btn-auth primary" onclick="openAuth('register')">Реєстрація</button>`;
    const saveBtn = document.getElementById('saveRouteBtn');
    if (saveBtn) saveBtn.style.display = 'none';
  }
}

function openAuth(tab) {
  document.getElementById('authModal').classList.add('visible');
  switchAuthTab(tab);
}
function closeAuth() { document.getElementById('authModal').classList.remove('visible'); }

function switchAuthTab(tab) {
  const tabs = ['login','register','forgot'];
  tabs.forEach(t => {
    const btn = document.getElementById('tab' + t.charAt(0).toUpperCase() + t.slice(1));
    if (btn) btn.classList.toggle('active', t === tab);
    const form = document.getElementById(t + 'Form');
    if (form) form.style.display = t === tab ? 'flex' : 'none';
  });
  document.getElementById('authTabs').style.display = tab === 'forgot' ? 'none' : 'flex';
  document.getElementById('authError').classList.remove('visible');
  document.getElementById('authSuccess').classList.remove('visible');
}

function _showAuthError(msg) {
  const e = document.getElementById('authError');
  e.textContent = msg; e.classList.add('visible');
  document.getElementById('authSuccess').classList.remove('visible');
}
function _showAuthSuccess(msg) {
  const e = document.getElementById('authSuccess');
  e.textContent = msg; e.classList.add('visible');
  document.getElementById('authError').classList.remove('visible');
}

function checkPasswordStrength(val, prefix = '') {
  const isReset = prefix === 'reset';
  const isW = prefix === 'w';
  const sfx = isReset ? 'R' : isW ? 'W' : '';
  const rules = [
    { id: 'hintLen'   + sfx, ok: val.length >= 8 },
    { id: 'hintUpper' + sfx, ok: /[A-Z]/.test(val) },
    { id: 'hintNum'   + sfx, ok: /[0-9]/.test(val) },
    { id: 'hintSpecial' + (isW ? 'W' : ''), ok: /[!@#$%^&*()_+\-=\[\]{};':"\\|,.<>\/?]/.test(val), skipReset: true },
  ];
  let score = 0;
  rules.forEach(r => {
    if (r.skipReset && isReset) return;
    const el = document.getElementById(r.id);
    if (!el) return;
    el.classList.toggle('ok', r.ok);
    el.textContent = (r.ok ? '✓ ' : '✗ ') + el.textContent.slice(2);
    if (r.ok) score++;
  });
  const maxScore = isReset ? 3 : 4;
  const fillId = isReset ? 'pwdStrengthFillReset' : isW ? 'pwdStrengthFillW' : 'pwdStrengthFill';
  const fill = document.getElementById(fillId);
  if (!fill) return;
  const pct = (score / maxScore) * 100;
  const color = score <= 1 ? '#f87171' : score === 2 ? '#f0b746' : score === 3 ? '#6ee7b7' : '#2ec98a';
  fill.style.width = pct + '%';
  fill.style.background = color;
  if (!isReset) checkPasswordMatch(isW ? 'w' : '');
}

function checkPasswordMatch(prefix = '') {
  const isW = prefix === 'w';
  const isReset = prefix === 'reset';
  const pwd  = document.getElementById(isReset ? 'resetPassword' : isW ? 'wRegPassword' : 'regPassword')?.value || '';
  const conf = document.getElementById(isReset ? 'resetPasswordConfirm' : isW ? 'wRegPasswordConfirm' : 'regPasswordConfirm')?.value || '';
  const msg  = document.getElementById(isReset ? 'pwdMatchMsgReset' : isW ? 'pwdMatchMsgW' : 'pwdMatchMsg');
  if (!msg || !conf) return;
  const ok = pwd === conf;
  msg.textContent = conf ? (ok ? '✓ Паролі збігаються' : '✗ Паролі не збігаються') : '';
  msg.style.color = ok ? 'var(--sage)' : '#f87171';
}

async function submitLogin(e) {
  e.preventDefault();
  const res = await fetch('/api/auth/login', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ email: document.getElementById('loginEmail').value, password: document.getElementById('loginPassword').value })
  });
  const data = await res.json();
  if (data.success) {
    _currentUser = data.user; _updateHeaderAuth(); closeAuth();
    if (_currentUser.interests && _currentUser.interests.length) _applyUserInterests(_currentUser.interests);
    showPage('search');
  } else _showAuthError(data.error);
}

async function submitRegister(e) {
  e.preventDefault();
  const pwd  = document.getElementById('regPassword').value;
  const conf = document.getElementById('regPasswordConfirm').value;
  if (pwd !== conf) { _showAuthError('Паролі не збігаються'); return; }
  if (pwd.length < 8) { _showAuthError('Пароль мінімум 8 символів'); return; }
  const res = await fetch('/api/auth/register', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ name: document.getElementById('regName').value, email: document.getElementById('regEmail').value, password: pwd })
  });
  const data = await res.json();
  if (data.success) { _currentUser = data.user; _updateHeaderAuth(); closeAuth(); showOnboarding(data.user.name); }
  else _showAuthError(data.error);
}

async function submitForgot(e) {
  e.preventDefault();
  const res = await fetch('/api/auth/forgot-password', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ email: document.getElementById('forgotEmail').value })
  });
  const data = await res.json();
  if (data.success) {
    const el = document.getElementById('authSuccess');
    el.classList.add('visible');
    document.getElementById('authError').classList.remove('visible');
    if (data._dev_token) {
      el.innerHTML = `${data.message}<br><br>
        <span style="opacity:0.7">SMTP не налаштовано — тестове посилання:</span><br>
        <a href="/?reset_token=${data._dev_token}" style="color:var(--gold);font-family:monospace;font-size:0.65rem;word-break:break-all;">
          Натисни для скидання пароля →
        </a>`;
    } else {
      el.textContent = data.message;
    }
  } else _showAuthError(data.error);
}

async function submitReset(e) {
  e.preventDefault();
  const pwd  = document.getElementById('resetPassword').value;
  const conf = document.getElementById('resetPasswordConfirm').value;
  const errFn = document.getElementById('welcomeError') ? _showWelcomeError : _showAuthError;
  if (pwd !== conf) { errFn('Паролі не збігаються'); return; }
  if (pwd.length < 8) { errFn('Пароль мінімум 8 символів'); return; }
  const res = await fetch('/api/auth/reset-password', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ token: _resetToken, password: pwd })
  });
  const data = await res.json();
  if (data.success) { _currentUser = data.user; _updateHeaderAuth(); closeAuth(); showPage('search'); }
  else errFn(data.error);
}

function toggleProfileMenu(e) {
  e.stopPropagation();
  const d = document.getElementById('profileDropdown');
  if (d) d.classList.toggle('open');
}
function closeProfileMenu() {
  const d = document.getElementById('profileDropdown');
  if (d) d.classList.remove('open');
}
document.addEventListener('click', closeProfileMenu);

async function doLogout() {
  await fetch('/api/auth/logout', { method: 'POST' });
  _currentUser = null;
  _updateHeaderAuth();
  showPage('welcome');
}

async function saveCurrentRoute(route, interests, budgetLvl) {
  if (!_currentUser) { openAuth('login'); return; }
  const res = await fetch('/api/routes/save', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ route, interests: interests || [], budget_level: budgetLvl || 'mid' })
  });
  const data = await res.json();
  if (data.success) {
    const btn = document.getElementById('saveRouteBtn');
    if (btn) { btn.textContent = '✓ Збережено'; btn.disabled = true; }
  }
}

async function openSavedPanel() {
  document.getElementById('savedPanel').classList.add('open');
  const res = await fetch('/api/routes/saved');
  const data = await res.json();
  const list = document.getElementById('savedList');
  if (!data.routes || !data.routes.length) {
    list.innerHTML = '<div style="font-family:\'DM Mono\',monospace;font-size:0.65rem;color:var(--mist);">Збережених маршрутів ще немає</div>';
    return;
  }
  list.innerHTML = data.routes.map(r => `
    <div class="saved-route-card">
      <div class="saved-route-main" onclick="loadSavedRoute(${r.id})">
        <button class="saved-route-delete" onclick="event.stopPropagation();deleteSavedRoute(${r.id},this)">✕</button>
        <div class="saved-route-title">${r.title}</div>
        <div class="saved-route-meta">
          <span>📍 ${r.destination || '—'}</span>
          <span>📅 ${r.duration || '—'}</span>
        </div>
      </div>
      <div class="src-actions">
        <button class="src-btn-rate" onclick="toggleReviewForm(${r.id})">Залишити відгук</button>
        <button class="src-btn-reviews" onclick="toggleRouteReviews(${r.id})">Відгуки</button>
      </div>
      <div class="route-reviews-section" id="route-reviews-${r.id}"></div>
      <div class="review-form" id="review-form-${r.id}">
        <div class="star-picker" id="star-picker-${r.id}">
          ${[1,2,3,4,5].map(n => `<span class="rstar" data-val="${n}" onclick="setStarRating(${r.id},${n})">★</span>`).join('')}
        </div>
        <p class="star-hint" id="star-hint-${r.id}">Оберіть оцінку</p>
        <textarea class="review-textarea" id="review-comment-${r.id}" placeholder="Ваш відгук (необов'язково)..." rows="2"></textarea>
        <button class="src-btn-submit" onclick="submitReview(${r.id})">Надіслати відгук</button>
      </div>
    </div>`).join('');
}

function closeSavedPanel() { document.getElementById('savedPanel').classList.remove('open'); }

async function loadSavedRoute(id) {
  const res = await fetch(`/api/routes/saved/${id}`);
  const data = await res.json();
  if (data.success) {
    closeSavedPanel();
    renderResult(data.route, [], [], null, null, data.route.transport || {}, null);
    showPage('results');
  }
}

async function deleteSavedRoute(id, btn) {
  await fetch(`/api/routes/saved/${id}`, { method: 'DELETE' });
  btn.closest('.saved-route-card').remove();
}

function toggleReviewForm(id) {
  const form = document.getElementById(`review-form-${id}`);
  if (!form) return;
  form.classList.toggle('open');
}

async function toggleRouteReviews(id) {
  const el = document.getElementById(`route-reviews-${id}`);
  if (!el) return;
  if (el.classList.contains('open')) {
    el.classList.remove('open');
    el.innerHTML = '';
    return;
  }
  el.innerHTML = '<p class="rpm-loading" style="padding:0.8rem 1rem;">Завантаження...</p>';
  el.classList.add('open');
  const res  = await fetch(`/api/reviews/${id}`);
  const data = await res.json();
  if (!data.reviews || !data.reviews.length) {
    el.innerHTML = '<p class="rpm-no-reviews" style="padding:0.8rem 1rem;">Відгуків ще немає</p>';
    return;
  }
  const stars = n => '★'.repeat(n) + '☆'.repeat(5 - n);
  el.innerHTML = `
    <div class="inline-reviews">
      <div class="rpm-reviews-header" style="margin-bottom:0.7rem;">
        <span class="rpm-avg-stars">${stars(Math.round(data.avg_rating))}</span>
        <span class="rpm-avg-score">${data.avg_rating}</span>
        <span class="rpm-reviews-count">${data.total} ${data.total < 5 ? 'відгуки' : 'відгуків'}</span>
      </div>
      ${data.reviews.map(r => `
        <div class="rpm-review-item">
          <div class="rpm-review-top">
            <span class="rpm-review-author">${r.author}</span>
            <span class="rpm-review-stars">${stars(r.rating)}</span>
            <span class="rpm-review-date">${r.date}</span>
          </div>
          ${r.comment ? `<p class="rpm-review-comment">${r.comment}</p>` : ''}
        </div>`).join('')}
    </div>`;
}

const _starRatings = {};

function setStarRating(routeId, val) {
  _starRatings[routeId] = val;
  const picker = document.getElementById(`star-picker-${routeId}`);
  const hint   = document.getElementById(`star-hint-${routeId}`);
  if (!picker) return;
  picker.querySelectorAll('.rstar').forEach(s => {
    s.classList.toggle('active', parseInt(s.dataset.val) <= val);
  });
  const labels = {1:'Погано',2:'Задовільно',3:'Непогано',4:'Добре',5:'Відмінно'};
  if (hint) hint.textContent = labels[val] || '';
}

async function submitReview(routeId) {
  const rating  = _starRatings[routeId];
  const comment = (document.getElementById(`review-comment-${routeId}`)?.value || '').trim();
  if (!rating) {
    alert('Оберіть оцінку від 1 до 5 зірок');
    return;
  }
  const res = await fetch('/api/reviews/add', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ route_id: routeId, rating, comment }),
  });
  const data = await res.json();
  if (data.success) {
    const form = document.getElementById(`review-form-${routeId}`);
    if (form) {
      form.innerHTML = `<div class="review-success">Дякуємо за відгук! ${'★'.repeat(rating)}${'☆'.repeat(5-rating)}</div>`;
    }
  } else {
    alert(data.error || 'Помилка при збереженні відгуку');
  }
}

let _pendingRouteParams = null;

async function openRecommendedRoute(id) {
  const res = await fetch(`/api/routes/preview/${id}`);
  const data = await res.json();
  if (!data.success) return;

  const route = data.route;

  // If the route has full AI data (days array) — render it fully
  if (route.days && route.days.length) {
    closeSavedPanel();
    renderResult(route, [], [], null, null, route.transport || {}, null);
    showPage('results');
    return;
  }

  // Store params for fillSimilarRoute()
  _pendingRouteParams = {
    destination: data.destination,
    duration:    data.duration,
    budget_level: data.budget_level,
    interests:   data.interests || [],
  };

  const budgeLabel = {'budget':'Бюджетний','mid':'Комфорт','premium':'Преміум'};
  const interestLabels = {
    history:'Історія', architecture:'Архітектура', food:'Гастрономія',
    art:'Мистецтво', culture:'Культура', beach:'Пляж', nature:'Природа',
    hiking:'Хайкінг', photography:'Фотографія', nightlife:'Нічне життя',
    romance:'Романтика', adventure:'Пригоди', wine:'Вино', music:'Музика',
    shopping:'Шопінг', cycling:'Велоспорт', design:'Дизайн', relaxation:'Відпочинок',
  };
  const interests = (data.interests || []).map(i => interestLabels[i] || i).join(', ');
  const highlights = (route.highlights || []);

  document.getElementById('rpmTitle').textContent = data.title;
  document.getElementById('rpmMeta').textContent = `${data.destination} · ${data.duration} · ${budgeLabel[data.budget_level] || data.budget_level}`;
  document.getElementById('rpmInterests').textContent = interests ? `Інтереси: ${interests}` : '';
  document.getElementById('rpmHighlights').innerHTML = highlights.length
    ? `<ul>${highlights.map(h => `<li>${h}</li>`).join('')}</ul>` : '';
  document.getElementById('rpmReviews').innerHTML = '<p class="rpm-loading">Завантаження відгуків...</p>';
  document.getElementById('routePreviewModal').classList.add('open');

  // Load reviews asynchronously
  fetch(`/api/reviews/${id}`)
    .then(r => r.json())
    .then(rv => {
      const el = document.getElementById('rpmReviews');
      if (!el) return;
      if (!rv.reviews || !rv.reviews.length) {
        el.innerHTML = '<p class="rpm-no-reviews">Відгуків ще немає</p>';
        return;
      }
      const stars = n => '★'.repeat(n) + '☆'.repeat(5 - n);
      el.innerHTML = `
        <div class="rpm-reviews-header">
          <span class="rpm-avg-stars">${stars(Math.round(rv.avg_rating))}</span>
          <span class="rpm-avg-score">${rv.avg_rating}</span>
          <span class="rpm-reviews-count">${rv.total} ${rv.total === 1 ? 'відгук' : rv.total < 5 ? 'відгуки' : 'відгуків'}</span>
        </div>
        <div class="rpm-reviews-list">
          ${rv.reviews.map(r => `
            <div class="rpm-review-item">
              <div class="rpm-review-top">
                <span class="rpm-review-author">${r.author}</span>
                <span class="rpm-review-stars">${stars(r.rating)}</span>
                <span class="rpm-review-date">${r.date}</span>
              </div>
              ${r.comment ? `<p class="rpm-review-comment">${r.comment}</p>` : ''}
            </div>`).join('')}
        </div>`;
    })
    .catch(() => {});
}

function closeRoutePreview() {
  document.getElementById('routePreviewModal').classList.remove('open');
}

// Mapping from English seed interests → Ukrainian form values
const _interestToFormValue = {
  food:'їжа', architecture:'архітектура', nature:'природа', culture:'культура',
  adventure:'пригоди', beach:'пляж', shopping:'шопінг', art:'мистецтво',
  history:'архітектура', nightlife:'культура', photography:'мистецтво',
  romance:'культура', hiking:'пригоди', cycling:'пригоди', wine:'їжа',
  music:'культура', design:'мистецтво', sea:'пляж', relaxation:'природа',
};

function fillSimilarRoute() {
  closeRoutePreview();
  showPage('search');
  if (!_pendingRouteParams) return;

  const p = _pendingRouteParams;

  // Fill destination — extract city before comma ("Рим, Італія" → "Рим")
  const destEl = document.getElementById('destination');
  if (destEl) destEl.value = (p.destination || '').split(',')[0].trim();

  // Fill duration
  const durEl = document.getElementById('duration');
  if (durEl) durEl.value = p.duration || '';

  // Select budget level card
  if (p.budget_level && typeof selectBudgetLevel === 'function') {
    selectBudgetLevel(p.budget_level);
  }

  // Activate matching interest tags in the search form
  const targetValues = new Set(
    (p.interests || []).map(i => _interestToFormValue[i] || i)
  );
  document.querySelectorAll('.form-panel .interest-tag').forEach(tag => {
    if (targetValues.has(tag.dataset.value)) {
      tag.classList.add('active');
    } else {
      tag.classList.remove('active');
    }
  });

  // Briefly highlight filled fields
  [destEl, durEl].forEach(el => {
    if (!el || !el.value) return;
    el.style.borderColor = 'var(--terracotta)';
    el.style.background  = 'rgba(80,104,240,0.1)';
    setTimeout(() => { el.style.borderColor = ''; el.style.background = ''; }, 2000);
  });
}

_checkAuth();
