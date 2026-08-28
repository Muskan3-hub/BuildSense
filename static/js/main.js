// BuildSense Client Controller

document.addEventListener("DOMContentLoaded", () => {
    // State management
    let state = {
        imagePath: "",
        fileUrl: "",
        spatialData: null,
        isConfigured: false,
        lastPipelineResult: null,
        currentUser: null
    };
    // Top-level modules (calendar + plan generator) live OUTSIDE this closure.
    // Expose the single state object globally so they share one source of truth;
    // it is never reassigned wholesale, only mutated per-property.
    window.state = state;

    async function checkAuth() {
        try {
            const res = await fetch("/api/auth/status");
            const data = await res.json();
            if (data.authenticated) {
                state.currentUser = data.user;
            } else {
                window.location.href = '/login';
            }
        } catch (e) {
            console.error("Auth check failed", e);
        }
    }
    checkAuth();

    // Add logout handler
    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) {
        logoutBtn.addEventListener("click", async () => {
            try {
                const res = await fetch("/api/auth/logout", { method: "POST" });
                if (res.ok) {
                    window.location.href = '/login';
                }
            } catch (e) {
                console.error("Logout failed", e);
            }
        });
    }

    // DOM Elements
    const elements = {
        uploadDropzone: document.getElementById("uploadDropzone"),
        fileInput: document.getElementById("fileInput"),
        loadDemoBtn: document.getElementById("loadDemoBtn"),
        canvasContainer: document.getElementById("canvasContainer"),
        canvasControls: document.getElementById("canvasControls"),
        lblTotalArea: document.getElementById("lblTotalArea"),
        lblCorridors: document.getElementById("lblCorridors"),
        lblExits: document.getElementById("lblExits"),
        blueprintCanvas: document.getElementById("blueprintCanvas"),
        canvasTooltip: document.getElementById("canvasTooltip"),

        // Chat Section
        chatHistory: document.getElementById("chatHistory"),
        chatInput: document.getElementById("chatInput"),
        sendBtn: document.getElementById("sendBtn"),
        clearChatBtn: document.getElementById("clearChatBtn"),
        presetQueryBtn: document.getElementById("presetQueryBtn"),
        exportReportBtn: document.getElementById("exportReportBtn"),

        // Settings Modal
        engineModeBadge: document.getElementById("engineModeBadge")
    };

    // Zoom state
    let zoomState = { level: 1, minZoom: 0.25, maxZoom: 5, step: 0.25, panX: 0, panY: 0, isPanning: false, startPanX: 0, startPanY: 0 };

    window.setZoom = function (newLevel) {
        zoomState.level = Math.max(zoomState.minZoom, Math.min(zoomState.maxZoom, newLevel));
        const canvas = elements.blueprintCanvas;
        const wrapper = document.getElementById('canvasWrapper');
        if (canvas && canvas._bufferWidth) {
            // Set CSS display size — canvas buffer stays at full resolution
            canvas.style.width = (canvas._bufferWidth * zoomState.level) + 'px';
            canvas.style.height = (canvas._bufferHeight * zoomState.level) + 'px';
        }
        if (wrapper) {
            wrapper.style.transform = `translate(${zoomState.panX}px, ${zoomState.panY}px)`;
        }
        const zoomLabel = document.getElementById('zoomLevel');
        if (zoomLabel) zoomLabel.textContent = Math.round(zoomState.level * 100) + '%';
    };
    // Canvas drawing context
    const ctx = elements.blueprintCanvas.getContext("2d");
    let bgImage = new Image();

    // Polyfill for CanvasRenderingContext2D.roundRect if not available
    if (typeof ctx.roundRect !== 'function') {
        ctx.roundRect = function (x, y, w, h, r) {
            if (typeof r === 'number') r = [r, r, r, r];
            const [tl, tr, br, bl] = r;
            this.moveTo(x + tl, y);
            this.lineTo(x + w - tr, y);
            this.quadraticCurveTo(x + w, y, x + w, y + tr);
            this.lineTo(x + w, y + h - br);
            this.quadraticCurveTo(x + w, y + h, x + w - br, y + h);
            this.lineTo(x + bl, y + h);
            this.quadraticCurveTo(x, y + h, x, y + h - bl);
            this.lineTo(x, y + tl);
            this.quadraticCurveTo(x, y, x + tl, y);
            this.closePath();
        };
    }

    // ----------------------------------------------------
    // Engine Status (Live/Simulation badge only — no key UI)
    // ----------------------------------------------------
    async function checkEngineStatus() {
        try {
            const res = await fetch("/api/config");
            const data = await res.json();
            state.isConfigured = data.is_configured;
            const badgeText = elements.engineModeBadge.querySelector(".mode-text");
            const badgeDot = elements.engineModeBadge.querySelector(".status-dot");
            if (state.isConfigured) {
                badgeText.textContent = "Live Mode";
                badgeDot.className = "status-dot live";
            } else {
                badgeText.textContent = "Simulation Mode";
                badgeDot.className = "status-dot pulsing";
            }
        } catch (e) {
            console.error("Failed to query API config:", e);
        }
    }
    checkEngineStatus();

    // ----------------------------------------------------
    // Weather Strip + Weather Card Logic
    // ----------------------------------------------------
    // Auto-detect user location via browser geolocation.
    // Coordinates drive the weather lookup; reverse geocoding only names the card.
    let _detectedCity = null;
    let _detectedCoords = null;
    let _locationReady = null;
    async function _detectCityFromCoords(lat, lon) {
        try {
            const res = await fetch(`https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`);
            const data = await res.json();
            const city = data.address?.city || data.address?.town || data.address?.village || data.address?.county || null;
            if (city) {
                _detectedCity = city;
                console.log('[BuildSense] Detected location:', city);
            }
        } catch (e) {
            console.log('[BuildSense] Reverse geocoding failed:', e.message);
        }
    }
    // Resolve device location once per session (never re-prompts mid-session).
    // Resolves true when coordinates are available, false on denied/unavailable/error.
    function _resolveLocationOnce() {
        if (_locationReady) return _locationReady;
        _locationReady = new Promise((resolve) => {
            if (!navigator.geolocation) {
                console.log('[BuildSense] Geolocation unsupported — using default location');
                return resolve(false);
            }
            navigator.geolocation.getCurrentPosition(
                async (pos) => {
                    _detectedCoords = { lat: pos.coords.latitude, lon: pos.coords.longitude };
                    await _detectCityFromCoords(pos.coords.latitude, pos.coords.longitude);
                    resolve(true);
                },
                () => {
                    console.log('[BuildSense] Geolocation denied/unavailable — using default location');
                    resolve(false);
                },
                { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
            );
        });
        return _locationReady;
    }

    let _weatherRequestId = 0;
    async function loadWeather(location) {
        // location: string → explicit city (manual Weather tab input);
        //           undefined → detected coordinates, else show loading/error
        const requestId = ++_weatherRequestId;
        let qs = "";
        if (typeof location === "string" && location.trim()) {
            qs = `?city=${encodeURIComponent(location.trim())}`;
        } else if (_detectedCoords) {
            const p = new URLSearchParams({ lat: _detectedCoords.lat, lon: _detectedCoords.lon });
            if (_detectedCity) p.set("city", _detectedCity);   // display name for the widget
            qs = `?${p.toString()}`;
        }
        const fromDetectedLocation = qs.includes("lat=");
        try {
            const res = await fetch(`/api/weather${qs}`);
            if (requestId !== _weatherRequestId) return; // stale request, discard
            if (!res.ok) {
                if (typeof showDashboardWeatherError === 'function') showDashboardWeatherError();
                return;
            }
            const data = await res.json();

            // Prefer the reverse-geocoded city for display; never show raw coordinates
            if (fromDetectedLocation && _detectedCity) data.city = _detectedCity;

            // Weather tab (no header strip — it was removed)
            const bigIcon = document.getElementById("weatherBigIcon");
            const cityName = document.getElementById("weatherCityName");
            const tempValue = document.getElementById("weatherTempValue");
            const conditionText = document.getElementById("weatherConditionText");
            const feelsLike = document.getElementById("weatherFeelsLike");
            const humidity = document.getElementById("weatherHumidity");
            const wind = document.getElementById("weatherWind");
            const rainfall = document.getElementById("weatherRainfall");
            const riskBadge = document.getElementById("weatherRiskBadge");
            const advisoryText = document.getElementById("weatherAdvisoryText");
            const sourceText = document.getElementById("weatherSource");

            if (bigIcon) bigIcon.textContent = data.condition_icon || "🌤️";
            if (cityName) cityName.textContent = data.city || "--";
            if (tempValue) tempValue.textContent = `${data.temp_c}°C`;
            if (conditionText) conditionText.textContent = data.condition || "--";
            if (feelsLike) feelsLike.textContent = `${data.feels_like_c}°C`;
            if (humidity) humidity.textContent = `${data.humidity_pct}%`;
            if (wind) wind.textContent = `${data.wind_speed_kmh} km/h`;
            if (rainfall) rainfall.textContent = `${data.rainfall_1h_mm} mm/h`;

            if (riskBadge) {
                riskBadge.textContent = data.risk_level;
                riskBadge.className = `weather-risk-badge risk-${data.risk_level.toLowerCase()}`;
            }
            if (advisoryText && data.advisories && data.advisories.length > 0) {
                advisoryText.textContent = data.advisories[0];
            }
            // Populate advisory list
            const advisoryList = document.getElementById('weatherAdvisoryList');
            if (advisoryList && data.advisories) {
                advisoryList.innerHTML = data.advisories.map(a =>
                    `<div class="advisory-item">${a}</div>`
                ).join('');
            }
            if (sourceText) sourceText.textContent = data.source || "";

            // Update dashboard weather card & popover
            if (typeof updateDashboardWeather === 'function') updateDashboardWeather(data);
        } catch (e) {
            console.error("Failed to load weather data:", e);
            const conditionText = document.getElementById("weatherConditionText");
            if (conditionText) conditionText.textContent = "Weather temporarily unavailable";
            if (typeof showDashboardWeatherError === 'function') showDashboardWeatherError();
        }
    }
    // Weather city input on weather tab
    const refreshWeatherBtn = document.getElementById("refreshWeatherBtn");
    const weatherCityInput = document.getElementById("weatherCityInput");
    if (refreshWeatherBtn && weatherCityInput) {
        refreshWeatherBtn.addEventListener("click", () => {
            const val = weatherCityInput.value.trim();
            loadWeather(val || _detectedCity || undefined);
        });
        weatherCityInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                const val = weatherCityInput.value.trim();
                loadWeather(val || _detectedCity || undefined);
            }
        });
    }

    // Load weather on demand (weather tab click)
    window.ensureWeatherLoaded = function () {
        const conditionText = document.getElementById('weatherConditionText');
        if (conditionText && conditionText.textContent === 'Loading...') loadWeather();
    };

    // ── Dashboard Weather Card + Popover ──
    let _lastWeatherData = null;

    function updateDashboardWeather(data) {
        _lastWeatherData = data;
        const city = data.city || (_detectedCity || '--');
        const icon = data.condition_icon || '🌤️';
        const temp = data.temp_c != null ? `${data.temp_c}°C` : '--';
        const condition = data.condition || '';
        const riskLevel = (data.risk_level || 'LOW').toUpperCase();
        const riskClass = `risk-${riskLevel.toLowerCase()}`;

        // Card elements
        const loadingEl = document.getElementById('dashboardWeatherLoading');
        const loadedEl = document.getElementById('dashboardWeatherLoaded');
        const errorEl = document.getElementById('dashboardWeatherError');
        if (loadingEl) loadingEl.style.display = 'none';
        if (errorEl) errorEl.style.display = 'none';
        if (loadedEl) loadedEl.style.display = '';
        const dwCity = document.getElementById('dwCity');
        const dwIcon = document.getElementById('dwIcon');
        const dwTemp = document.getElementById('dwTemp');
        const dwRisk = document.getElementById('dwRisk');
        if (dwCity) dwCity.textContent = city;
        if (dwIcon) dwIcon.textContent = icon;
        if (dwTemp) dwTemp.textContent = temp;
        if (dwRisk) {
            dwRisk.textContent = riskLevel;
            dwRisk.className = `dw-risk ${riskClass}`;
        }

        // Popover elements
        const wpCity = document.getElementById('wpCity');
        const wpIcon = document.getElementById('wpIcon');
        const wpTemp = document.getElementById('wpTemp');
        const wpCondition = document.getElementById('wpCondition');
        const wpFeelsLike = document.getElementById('wpFeelsLike');
        const wpHumidity = document.getElementById('wpHumidity');
        const wpWind = document.getElementById('wpWind');
        const wpRainfall = document.getElementById('wpRainfall');
        const wpUpdated = document.getElementById('wpUpdated');
        const wpRisk = document.getElementById('wpRisk');
        const wpAdvisories = document.getElementById('wpAdvisories');
        const wpSafe = document.getElementById('wpSafeActivities');
        const wpRestricted = document.getElementById('wpRestrictedActivities');
        if (wpCity) wpCity.textContent = city;
        if (wpIcon) wpIcon.textContent = icon;
        if (wpTemp) wpTemp.textContent = temp;
        if (wpCondition) wpCondition.textContent = condition;
        if (wpFeelsLike) wpFeelsLike.textContent = data.feels_like_c != null ? `${data.feels_like_c}°C` : '--';
        if (wpHumidity) wpHumidity.textContent = data.humidity_pct != null ? `${data.humidity_pct}%` : '--';
        if (wpWind) wpWind.textContent = data.wind_speed_kmh != null ? `${data.wind_speed_kmh} km/h` : '--';
        if (wpRainfall) wpRainfall.textContent = data.rainfall_1h_mm != null ? `${data.rainfall_1h_mm} mm/h` : '--';
        if (wpRisk) {
            wpRisk.textContent = riskLevel;
            wpRisk.className = `wp-risk ${riskClass}`;
        }
        // Construction advisories
        if (wpAdvisories && data.advisories) {
            wpAdvisories.innerHTML = data.advisories.map(a => `<div style="margin-bottom:4px;">${a}</div>`).join('');
        }
        if (wpSafe && data.safe_activities && data.safe_activities.length) {
            wpSafe.innerHTML = `<span class="wp-act-label">✅ Safe Activities</span>${data.safe_activities.join(' • ')}`;
        }
        if (wpRestricted && data.restricted_activities && data.restricted_activities.length) {
            wpRestricted.innerHTML = `<span class="wp-act-label">⛔ Restricted Activities</span>${data.restricted_activities.join(' • ')}`;
        }
        if (wpUpdated) {
            wpUpdated.textContent = 'Updated just now';
            wpUpdated._loadTime = Date.now();
        }
    }
    function showDashboardWeatherError() {
        const loadingEl = document.getElementById('dashboardWeatherLoading');
        const loadedEl = document.getElementById('dashboardWeatherLoaded');
        const errorEl = document.getElementById('dashboardWeatherError');
        if (loadingEl) loadingEl.style.display = 'none';
        if (loadedEl) loadedEl.style.display = 'none';
        if (errorEl) errorEl.style.display = '';
    }

    // Auto-load weather once the location attempt settles (detected coords if
    // granted, otherwise the existing default) — a single fetch, no race.
    _resolveLocationOnce().then(() => loadWeather());
    // Refresh weather data periodically (every 10 minutes, reusing last known location)
    setInterval(() => loadWeather(), 600000);
    // Update "Updated X ago" text every 60 seconds
    setInterval(() => {
        const wpUpdated = document.getElementById('wpUpdated');
        if (wpUpdated && wpUpdated._loadTime) {
            const mins = Math.round((Date.now() - wpUpdated._loadTime) / 60000);
            wpUpdated.textContent = mins < 1 ? 'Updated just now' : `Updated ${mins} min ago`;
        }
    }, 60000);

    // ── Weather Card Interaction ──
    const dashboardWeatherCard = document.getElementById('dashboardWeatherCard');
    const weatherPopover = document.getElementById('weatherPopover');

    function closeWeatherPopover() {
        if (weatherPopover) weatherPopover.classList.remove('open');
        if (dashboardWeatherCard) dashboardWeatherCard.setAttribute('aria-expanded', 'false');
    }
    function toggleWeatherPopover(e) {
        if (e) e.stopPropagation();
        if (!weatherPopover) return;
        const isOpen = weatherPopover.classList.contains('open');
        if (isOpen) {
            closeWeatherPopover();
        } else {
            weatherPopover.classList.add('open');
            if (dashboardWeatherCard) dashboardWeatherCard.setAttribute('aria-expanded', 'true');
        }
    }
    if (dashboardWeatherCard) {
        dashboardWeatherCard.addEventListener('click', toggleWeatherPopover);
        dashboardWeatherCard.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggleWeatherPopover();
            }
            if (e.key === 'Escape') closeWeatherPopover();
        });
    }
    // Close popover on outside click or escape
    document.addEventListener('click', (e) => {
        if (weatherPopover && weatherPopover.classList.contains('open')) {
            if (dashboardWeatherCard && !dashboardWeatherCard.contains(e.target)) {
                closeWeatherPopover();
            }
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeWeatherPopover();
    });

    // Expose setZoom for external callers
    window._zoomState = zoomState;

    // ----------------------------------------------------
    // Floor Plan Blueprint Visualizer (Canvas Rendering)
    // ----------------------------------------------------
    //
    // COORDINATE SYSTEM:
    //   Backend returns percentages [0-100] for x, y, width, height.
    //   Canvas buffer = natural image dimensions.
    //   Conversion: pixelX = (percentX / 100) * canvas.width
    //   Grid, rooms, corridors, exits all share the same canvas space.
    //   Zoom scales the canvas display via CSS width/height.
    //   All overlays are part of the canvas, so they scale together.
    //

    // ── Helper: draw a multi-line label with background pill ──
    function _drawLabel(lines, x, y, w, h, nameFont, dimFont, lineH, nameColor, dimColor, bgColor, padding) {
        padding = padding || 6;
        nameFont = nameFont || "bold 11px Outfit";
        dimFont = dimFont || "9px Inter";
        lineH = lineH || 14;
        nameColor = nameColor || "#00d2ff";
        dimColor = dimColor || "rgba(0, 210, 255, 0.7)";
        bgColor = bgColor || "rgba(0, 0, 0, 0.55)";

        // Measure max line width
        let maxW = 0;
        ctx.font = nameFont;
        maxW = Math.max(maxW, ctx.measureText(lines[0]).width);
        if (lines.length > 1) {
            ctx.font = dimFont;
            for (let i = 1; i < lines.length; i++) {
                maxW = Math.max(maxW, ctx.measureText(lines[i]).width);
            }
        }

        const totalH = lines.length * lineH + padding;
        const labelW = maxW + padding * 2;

        // Draw background pill
        ctx.fillStyle = bgColor;
        ctx.beginPath();
        ctx.roundRect(x, y, labelW, totalH, 5);
        ctx.fill();

        // Draw each line
        lines.forEach((line, i) => {
            ctx.font = i === 0 ? nameFont : dimFont;
            ctx.fillStyle = i === 0 ? nameColor : dimColor;
            const lw = ctx.measureText(line).width;
            ctx.fillText(line, x + (labelW - lw) / 2, y + padding / 2 + (i + 1) * lineH - 2);
        });

        return { w: labelW, h: totalH };
    }

    // ── Helper: find the best position for a label inside/around a room ──
    //   Tries: center → top-center → bottom-center → left → right
    //   with collision avoidance against already-placed labels.
    //   Returns {x, y} in canvas pixels, clamped to canvas bounds.
    function _findLabelPos(labelW, labelH, rx, ry, rw, rh, cw, ch, placedLabels) {
        const PAD = 6;
        const cW = cw;
        const cH = ch;

        // Candidate positions: [x, y] relative to canvas
        const candidates = [
            // 1. Centered inside room
            { x: rx + (rw - labelW) / 2, y: ry + (rh - labelH) / 2 },
            // 2. Top-left inside room
            { x: rx + PAD, y: ry + PAD },
            // 3. Top-center inside room
            { x: rx + (rw - labelW) / 2, y: ry + PAD },
            // 4. Bottom-center inside room
            { x: rx + (rw - labelW) / 2, y: ry + rh - labelH - PAD },
            // 5. Below room
            { x: rx + (rw - labelW) / 2, y: ry + rh + PAD },
            // 6. Above room
            { x: rx + (rw - labelW) / 2, y: ry - labelH - PAD },
            // 7. Right of room
            { x: rx + rw + PAD, y: ry + (rh - labelH) / 2 },
            // 8. Left of room
            { x: rx - labelW - PAD, y: ry + (rh - labelH) / 2 },
            // 9. Top-right inside room
            { x: rx + rw - labelW - PAD, y: ry + PAD },
            // 10. Bottom-right inside room
            { x: rx + rw - labelW - PAD, y: ry + rh - labelH - PAD },
            // 11. Top-left outside (below + left)
            { x: rx - labelW - PAD, y: ry + rh + PAD },
            // 12. Top-right outside (below + right)
            { x: rx + rw + PAD, y: ry + rh + PAD },
        ];

        // Helper: check overlap between two rectangles
        function rectsOverlap(ax, ay, aw, ah, bx, by, bw, bh) {
            return ax < bx + bw && ax + aw > bx && ay < by + bh && ay + ah > by;
        }

        // Check each candidate: must be inside canvas and not overlap existing labels
        for (const c of candidates) {
            // Clamp to canvas bounds
            const cx = Math.max(2, Math.min(cW - labelW - 2, c.x));
            const cy = Math.max(2, Math.min(cH - labelH - 2, c.y));

            // Skip if extends outside canvas
            if (cx + labelW > cW || cy + labelH > cH) continue;
            if (cx < 0 || cy < 0) continue;

            // Check collision with already-placed labels
            let collision = false;
            for (const p of placedLabels) {
                if (rectsOverlap(cx, cy, labelW, labelH, p.x, p.y, p.w, p.h)) {
                    collision = true;
                    break;
                }
            }
            if (!collision) return { x: cx, y: cy };
        }

        // Fallback: clamp to canvas center, even if it overlaps
        return {
            x: Math.max(2, Math.min(cW - labelW - 2, (cW - labelW) / 2)),
            y: Math.max(2, Math.min(cH - labelH - 2, (cH - labelH) / 2))
        };
    }

    function renderBlueprint() {
        if (!state.spatialData) return;

        const canvas = elements.blueprintCanvas;
        const cw = bgImage.naturalWidth || 800;
        const ch = bgImage.naturalHeight || 600;
        canvas.width = cw;
        canvas.height = ch;
        canvas._bufferWidth = cw;
        canvas._bufferHeight = ch;

        // Draw background image at full natural size
        ctx.clearRect(0, 0, cw, ch);
        ctx.drawImage(bgImage, 0, 0, cw, ch);

        // ── Grid Overlay ──
        // Spacing scaled to image size; grid origin = image origin
        const gridSpacing = Math.max(40, Math.floor(Math.min(cw, ch) / 20));
        ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
        ctx.lineWidth = 0.5;
        for (let gx = 0; gx <= cw; gx += gridSpacing) {
            ctx.beginPath();
            ctx.moveTo(gx, 0);
            ctx.lineTo(gx, ch);
            ctx.stroke();
        }
        for (let gy = 0; gy <= ch; gy += gridSpacing) {
            ctx.beginPath();
            ctx.moveTo(0, gy);
            ctx.lineTo(cw, gy);
            ctx.stroke();
        }

        // ── Outer Boundary (dashed) ──
        let allCoords = [];
        (state.spatialData.rooms || []).forEach(r => allCoords.push(r.coords));
        (state.spatialData.corridors || []).forEach(r => allCoords.push(r.coords));
        if (allCoords.length > 0) {
            let minX = 100, minY = 100, maxX = 0, maxY = 0;
            allCoords.forEach(([rx, ry, rw, rh]) => {
                if (rx < minX) minX = rx;
                if (ry < minY) minY = ry;
                if (rx + rw > maxX) maxX = rx + rw;
                if (ry + rh > maxY) maxY = ry + rh;
            });
            const pad = 2;
            minX = Math.max(0, minX - pad);
            minY = Math.max(0, minY - pad);
            maxX = Math.min(100, maxX + pad);
            maxY = Math.min(100, maxY + pad);
            const bx = (minX / 100) * cw;
            const by = (minY / 100) * ch;
            const bw = ((maxX - minX) / 100) * cw;
            const bh = ((maxY - minY) / 100) * ch;
            ctx.strokeStyle = "rgba(255, 255, 255, 0.25)";
            ctx.lineWidth = 1.5;
            ctx.setLineDash([6, 4]);
            ctx.strokeRect(bx, by, bw, bh);
            ctx.setLineDash([]);
        }

        // ── Track placed labels for collision avoidance ──
        const placedLabels = [];

        // ── Helper: convert percentage coords to canvas pixels ──
        const pct2px = ([rx, ry, rw, rh]) => ({
            x: (rx / 100) * cw,
            y: (ry / 100) * ch,
            w: (rw / 100) * cw,
            h: (rh / 100) * ch,
        });

        // ── 1. Corridors (orange) — drawn first ──
        (state.spatialData.corridors || []).forEach(corr => {
            const { x, y, w, h } = pct2px(corr.coords);

            ctx.fillStyle = "rgba(255, 170, 0, 0.18)";
            ctx.strokeStyle = "rgba(255, 170, 0, 0.85)";
            ctx.lineWidth = 2.5;
            ctx.fillRect(x, y, w, h);
            ctx.strokeRect(x, y, w, h);

            // Inner dashed boundary
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = "rgba(255, 170, 0, 0.4)";
            ctx.lineWidth = 1;
            ctx.strokeRect(x + 4, y + 4, w - 8, h - 8);
            ctx.setLineDash([]);

            // Corridor label
            const lines = [corr.name];
            if (corr.width_m) lines.push(`W: ${corr.width_m}m`);
            if (corr.length_m) lines.push(`L: ${corr.length_m}m`);
            const nameFont = "bold 10px Outfit";
            const dimFont = "9px Inter";
            const lineH = 13;
            // Measure label size
            let maxW = 0;
            ctx.font = nameFont;
            maxW = Math.max(maxW, ctx.measureText(lines[0]).width);
            if (lines.length > 1) {
                ctx.font = dimFont;
                for (let i = 1; i < lines.length; i++) {
                    maxW = Math.max(maxW, ctx.measureText(lines[i]).width);
                }
            }
            const totalH = lines.length * lineH + 6;
            const labelW = maxW + 12;
            // Use _findLabelPos for collision avoidance
            const cPos = _findLabelPos(labelW, totalH, x, y, w, h, cw, ch, placedLabels);
            _drawLabel(lines, cPos.x, cPos.y, labelW, totalH, nameFont, dimFont, lineH, "#ffaa00", "rgba(255, 170, 0, 0.75)", "rgba(0, 0, 0, 0.65)", 6);
            placedLabels.push({ x: cPos.x, y: cPos.y, w: labelW, h: totalH });
        });

        // ── 2. Rooms (blue) ──
        (state.spatialData.rooms || []).forEach(room => {
            const { x, y, w, h } = pct2px(room.coords);
            // Room fill
            ctx.fillStyle = "rgba(0, 210, 255, 0.12)";
            ctx.fillRect(x, y, w, h);
            // Outer solid boundary
            ctx.strokeStyle = "rgba(0, 210, 255, 0.9)";
            ctx.lineWidth = 2;
            ctx.strokeRect(x, y, w, h);
            // Inner subtle boundary
            ctx.strokeStyle = "rgba(0, 210, 255, 0.25)";
            ctx.lineWidth = 1;
            ctx.strokeRect(x + 3, y + 3, w - 6, h - 6);
            // Corner markers
            const cm = 5;
            ctx.fillStyle = "rgba(0, 210, 255, 0.6)";
            ctx.fillRect(x - cm / 2, y - cm / 2, cm, cm);
            ctx.fillRect(x + w - cm / 2, y - cm / 2, cm, cm);
            ctx.fillRect(x - cm / 2, y + h - cm / 2, cm, cm);
            ctx.fillRect(x + w - cm / 2, y + h - cm / 2, cm, cm);

            // Build label lines
            const rLines = [room.name];
            if (room.dimensions) {
                let dimText = '';
                if (typeof room.dimensions === 'string') {
                    dimText = room.dimensions;
                } else if (room.dimensions.length && room.dimensions.width) {
                    dimText = `${room.dimensions.length} x ${room.dimensions.width} ${room.dimensions.unit || 'ft'}`;
                }
                if (dimText && room.area_sqft) {
                    dimText += ` (${room.area_sqft} sqft)`;
                } else if (room.area_sqft) {
                    dimText = `${room.area_sqft} sqft`;
                }
                if (dimText) rLines.push(dimText);
            } else if (room.area_sqft) {
                rLines.push(`${room.area_sqft} sqft`);
            }

            // Measure label size
            const nameFont = "bold 11px Outfit";
            const dimFont = "9px Inter";
            const lineH = 16;
            let maxW = 0;
            ctx.font = nameFont;
            maxW = Math.max(maxW, ctx.measureText(rLines[0]).width);
            if (rLines.length > 1) {
                ctx.font = dimFont;
                for (let i = 1; i < rLines.length; i++) {
                    maxW = Math.max(maxW, ctx.measureText(rLines[i]).width);
                }
            }
            const totalH = rLines.length * lineH + 6;
            const labelW = maxW + 12;

            // Smart position: try to place inside room, then fallback outside
            const pos = _findLabelPos(labelW, totalH, x, y, w, h, cw, ch, placedLabels);
            _drawLabel(rLines, pos.x, pos.y, labelW, totalH, nameFont, dimFont, lineH);
            placedLabels.push({ x: pos.x, y: pos.y, w: labelW, h: totalH });
        });

        // ── 3. Exits (green) ──
        (state.spatialData.exits || []).forEach(ex => {
            const { x, y, w, h } = pct2px(ex.coords);

            ctx.fillStyle = "rgba(0, 255, 170, 0.25)";
            ctx.strokeStyle = "rgba(0, 255, 170, 0.9)";
            ctx.lineWidth = 3;
            ctx.fillRect(x, y, w, h);
            ctx.strokeRect(x, y, w, h);

            // Exit label — use _findLabelPos for collision avoidance
            const eLabel = ex.name || 'Exit';
            const eFont = "bold 10px Outfit";
            ctx.font = eFont;
            const eTextW = ctx.measureText(eLabel).width;
            const eLW = eTextW + 10;
            const eLH = 18;
            const ePos = _findLabelPos(eLW, eLH, x, y, w, h, cw, ch, placedLabels);
            ctx.fillStyle = "rgba(0, 0, 0, 0.6)";
            ctx.beginPath();
            ctx.roundRect(ePos.x, ePos.y, eLW, eLH, 3);
            ctx.fill();
            ctx.fillStyle = "#00ffaa";
            ctx.fillText(eLabel, ePos.x + 5, ePos.y + 13);
            placedLabels.push({ x: ePos.x, y: ePos.y, w: eLW, h: eLH });
        });
    }

    // Mouse hover listener on Canvas for tooltips
    elements.blueprintCanvas.addEventListener("mousemove", (e) => {
        if (!state.spatialData) return;

        const canvas = elements.blueprintCanvas;
        const rect = canvas.getBoundingClientRect();

        // Calculate coordinate percentages
        const clickX = ((e.clientX - rect.left) / rect.width) * 100;
        const clickY = ((e.clientY - rect.top) / rect.height) * 100;

        let hoveredItem = null;
        let itemType = "";

        // Check Rooms
        (state.spatialData.rooms || []).forEach(room => {
            const [rx, ry, rw, rh] = room.coords;
            if (clickX >= rx && clickX <= rx + rw && clickY >= ry && clickY <= ry + rh) {
                hoveredItem = room;
                itemType = "room";
            }
        });

        // Check Corridors
        if (!hoveredItem) {
            (state.spatialData.corridors || []).forEach(corr => {
                const [rx, ry, rw, rh] = corr.coords;
                if (clickX >= rx && clickX <= rx + rw && clickY >= ry && clickY <= ry + rh) {
                    hoveredItem = corr;
                    itemType = "corridor";
                }
            });
        }

        // Check Exits
        if (!hoveredItem) {
            (state.spatialData.exits || []).forEach(ex => {
                const [rx, ry, rw, rh] = ex.coords;
                if (clickX >= rx && clickX <= rx + rw && clickY >= ry && clickY <= ry + rh) {
                    hoveredItem = ex;
                    itemType = "exit";
                }
            });
        }

        const tooltip = elements.canvasTooltip;
        if (hoveredItem) {
            // Show Tooltip
            tooltip.style.display = "block";
            tooltip.style.left = `${e.clientX - rect.left + 15}px`;
            tooltip.style.top = `${e.clientY - rect.top + 15}px`;

            if (itemType === "room") {
                let dimStr = hoveredItem.dimensions || '—';
                if (typeof dimStr === 'object') {
                    if (dimStr.length && dimStr.width) {
                        dimStr = `${dimStr.length} x ${dimStr.width} ${dimStr.unit || 'ft'}`;
                    } else {
                        dimStr = JSON.stringify(dimStr);
                    }
                }
                const areaStr = hoveredItem.area_sqft ? `${hoveredItem.area_sqft} sq ft` : '—';
                tooltip.innerHTML = `
                    <h4><i class="fa-solid fa-cube"></i> ${hoveredItem.name}</h4>
                    <p><strong>Dimensions:</strong> ${dimStr}</p>
                    <p><strong>Area:</strong> ${areaStr}</p>
                `;
            } else if (itemType === "corridor") {
                tooltip.innerHTML = `
                    <h4><i class="fa-solid fa-route"></i> ${hoveredItem.name}</h4>
                    <p><strong>Width:</strong> ${hoveredItem.width_m} meters</p>
                    <p><strong>Length:</strong> ${hoveredItem.length_m} meters</p>
                `;
            } else if (itemType === "exit") {
                tooltip.innerHTML = `
                    <h4><i class="fa-solid fa-door-open"></i> ${hoveredItem.name}</h4>
                    <p><strong>Type:</strong> ${hoveredItem.type.replace("_", " ")}</p>
                `;
            }
        } else {
            tooltip.style.display = "none";
        }
    });

    elements.blueprintCanvas.addEventListener("mouseleave", () => {
        elements.canvasTooltip.style.display = "none";
    });



    // ----------------------------------------------------
    // Uploader Logic
    // ----------------------------------------------------
    async function handleBlueprintData(data, options) {
        const silent = !!(options && options.silent);
        state.imagePath = data.image_path;
        state.fileUrl = data.file_url;
        state.spatialData = data.spatial_data;
        window.currentImagePath = data.image_path;
        window.currentSpatialData = data.spatial_data;

        const totalArea = state.spatialData.total_area_sqft;
        elements.lblTotalArea.innerHTML =
            `<i class="fa-solid fa-ruler-combined"></i> ${totalArea !== null && totalArea !== undefined
                ? Number(totalArea).toLocaleString() + " sq ft"
                : "Area not detected"
            }`;
        elements.lblCorridors.innerHTML = `<i class="fa-solid fa-route"></i> ${state.spatialData.corridors.length} Corridor(s)`;
        elements.lblExits.innerHTML = `<i class="fa-solid fa-door-open"></i> ${state.spatialData.exits.length} Exit(s)`;

        elements.uploadDropzone.style.display = "none";
        elements.canvasContainer.style.display = "flex";
        elements.canvasControls.style.display = "flex";

        // Always hide scanning overlay — it can be left visible if a prior
        // upload was in progress when this conversation was switched to.
        const _scanOvl = document.getElementById('workspaceScanningOverlay');
        if (_scanOvl) _scanOvl.style.display = 'none';
        const _uploadBar = document.getElementById('uploadProgressBar');
        if (_uploadBar) _uploadBar.style.display = 'none';

        // Reset zoom state
        zoomState.panX = 0; zoomState.panY = 0; zoomState.level = 1;

        // Load background image and fit to screen.
        // A FRESH Image object per render: reusing the module-level image
        // meant an already-complete previous blueprint could be drawn while
        // the newly requested file was still downloading (stale canvas).
        let blueprintRendered = false;
        const drawWhenReady = () => {
            if (blueprintRendered) return;
            blueprintRendered = true;
            renderBlueprint();
            console.debug('[BuildSense] Workspace render completed for', state.fileUrl);
        };
        bgImage = new Image();
        bgImage.onload = drawWhenReady;
        bgImage.onerror = () => {
            if (blueprintRendered) return;
            blueprintRendered = true;
            console.error('[BuildSense] Blueprint file failed to load:', state.fileUrl);
            addChatMessage('system', '<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> The stored blueprint file could not be loaded into the workspace.</p>');
        };
        bgImage.src = state.fileUrl;
        // If the browser has it cached, onload may fire synchronously — cover both paths
        if (bgImage.complete && bgImage.naturalWidth > 0) {
            drawWhenReady();
        }

        const areaRequest = state.spatialData.area_request;
        const areaSummary = totalArea !== null && totalArea !== undefined
            ? `${Number(totalArea).toLocaleString()} sq ft`
            : "Area not detected";
        const areaFallback = areaRequest?.required
            ? `<p><strong>Area input needed:</strong> ${areaRequest.message}</p>
               <p>Examples: ${(areaRequest.accepted_examples || []).join(", ")}</p>`
            : "";

        // Add a message in chat indicating blueprint ingested (skipped when
        // silently restoring a saved blueprint from an existing conversation)
        if (!silent) {
            addChatMessage("system", `
                <h3>Blueprint Ingested Successfully</h3>
                <p><strong>Total Area:</strong> ${areaSummary}</p>
                <p><strong>Structure:</strong> Extracted ${state.spatialData.rooms.length} rooms and ${state.spatialData.corridors.length} main corridor pathway.</p>
                <p>${state.spatialData.raw_analysis}</p>
                ${areaFallback}
            `);
        }

        window.parsedBlueprintData = state.spatialData;
        try {
            sessionStorage.setItem('buildsense_blueprint', JSON.stringify(state.spatialData));
        } catch (e) { }

        // Phase 1: Defer Calendar API Execution — Auto-trigger removed from blueprint ingest.
        // Workforce/calendar fetch will run when the user opens the Calendar tab.
        // refreshWorkerMinimum();
    }
    window.handleBlueprintData = handleBlueprintData; // expose to window scope

    async function uploadFile(file) {
        // Ensure a conversation exists so the blueprint isn't orphaned in the DB
        if (!window._currentConvId) {
            await ensureConversation();
        }

        const formData = new FormData();
        formData.append("file", file);
        if (window._currentConvId) formData.append("conversation_id", window._currentConvId);

        // Show upload progress & workspace scanning overlay
        document.getElementById('uploadProgressBar').style.display = 'block';
        document.getElementById('uploadDropzone').style.display = 'none';
        const scanOverlay = document.getElementById('workspaceScanningOverlay');
        if (scanOverlay) scanOverlay.style.display = 'flex';

        addChatMessage("system", "<p><i class='fa-solid fa-spinner fa-spin'></i> Uploading floor plan drawing to server...</p>");

        // Race condition prevention + AbortController
        const uploadId = Date.now();
        window._currentUploadId = uploadId;
        const controller = new AbortController();
        window._uploadAbortController = controller;

        try {
            const res = await fetch("/api/upload", {
                method: "POST",
                body: formData,
                signal: controller.signal
            });
            document.getElementById('uploadProgressBar').style.display = 'none';
            if (scanOverlay) scanOverlay.style.display = 'none';
            window._uploadAbortController = null;
            if (window._currentUploadId !== uploadId) return;
            const data = await res.json();
            if (data.error) {
                addChatMessage("system", `<p class="text-danger"><i class='fa-solid fa-circle-exclamation'></i> Error: ${data.error}</p>`);
                document.getElementById('uploadDropzone').style.display = '';
            } else {
                addChatMessage("system", "<p><i class='fa-solid fa-circle-check text-success'></i> Blueprint analysis complete. Results ingested into the workspace.</p>");
                if (data.blueprint_id) window._currentBlueprintId = data.blueprint_id;
                handleBlueprintData(data);
            }
        } catch (e) {
            document.getElementById('uploadProgressBar').style.display = 'none';
            if (scanOverlay) scanOverlay.style.display = 'none';
            window._uploadAbortController = null;
            if (window._currentUploadId !== uploadId) return;
            if (e.name === 'AbortError') {
                // User clicked Stop — not an error
                document.getElementById('uploadDropzone').style.display = '';
                addChatMessage("system", "<p><i class='fa-solid fa-ban'></i> Upload cancelled</p>");
                return;
            }
            addChatMessage("system", `<p class="text-danger"><i class='fa-solid fa-circle-exclamation'></i> Upload Failed: ${e.message}</p>`);
            document.getElementById('uploadDropzone').style.display = '';
        }
    }

    // Drag-and-drop events
    elements.uploadDropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        elements.uploadDropzone.style.borderColor = "var(--color-blue)";
        elements.uploadDropzone.style.backgroundColor = "rgba(0, 210, 255, 0.05)";
    });

    elements.uploadDropzone.addEventListener("dragleave", () => {
        elements.uploadDropzone.style.borderColor = "var(--border-glass)";
        elements.uploadDropzone.style.backgroundColor = "transparent";
    });

    elements.uploadDropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        elements.uploadDropzone.style.borderColor = "var(--border-glass)";
        elements.uploadDropzone.style.backgroundColor = "transparent";
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            uploadFile(files[0]);
        }
    });

    elements.fileInput.addEventListener("change", (e) => {
        const files = e.target.files;
        if (files.length > 0) {
            uploadFile(files[0]);
        }
    });

    // Preset Demo
    elements.loadDemoBtn.addEventListener("click", (e) => {
        e.stopPropagation(); // Avoid triggering file input click
        loadDemoBlueprint();
    });

    async function loadDemoBlueprint() {
        addChatMessage("system", "<p><i class='fa-solid fa-spinner fa-spin'></i> Loading demo blueprint (sample schematic upload + real analysis)...</p>");
        try {
            // Fetch the clearly-labelled sample asset and run it through the
            // REAL upload + analysis flow — no fabricated data is injected.
            const assetRes = await fetch("/static/demo/mock_blueprint.png");
            if (!assetRes.ok) throw new Error("Demo blueprint asset is not available on the server.");
            const blob = await assetRes.blob();
            const file = new File([blob], "demo_blueprint.png", { type: blob.type || "image/png" });
            await uploadFile(file);
        } catch (e) {
            addChatMessage("system", `<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> Demo Load Failed: ${e.message}</p>`);
        }
    }

    // ----------------------------------------------------
    // Multi-Agent Map Animations
    // ----------------------------------------------------
    // ----------------------------------------------------
    // Chat System & API Queries
    // ----------------------------------------------------
    function addChatMessage(sender, contentHTML, followUps, msgId) {
        const messageDiv = document.createElement("div");
        messageDiv.className = `chat-message ${sender === "user" ? "user-message" : "system-message"}`;
        if (msgId) messageDiv.dataset.msgId = String(msgId);

        const icon = sender === "user" ? "fa-user" : "fa-robot";
        const copyBtn = sender === "system" ? `<button class="copy-msg-btn" title="Copy response"><i class="fa-regular fa-copy"></i></button>` : '';
        const editBtn = sender === "user" ? `<button class="msg-edit-btn" title="Edit message"><i class="fa-solid fa-pen"></i> Edit</button>` : '';
        const followUpHTML = (followUps && followUps.length) ? `
            <div class="follow-up-suggestions">
                ${followUps.map(f => `<button class="follow-up-chip">${f}</button>`).join('')}
            </div>` : '';

        messageDiv.innerHTML = `
            <div class="msg-icon"><i class="fa-solid ${icon}"></i></div>
            <div class="msg-content">${contentHTML}${copyBtn}${editBtn}${followUpHTML}</div>
        `;

        // Bind copy button
        const copyBtnEl = messageDiv.querySelector('.copy-msg-btn');
        if (copyBtnEl) {
            copyBtnEl.addEventListener('click', () => {
                const textContent = messageDiv.querySelector('.msg-content')?.innerText || '';
                navigator.clipboard.writeText(textContent.replace('Copy\n', '').trim()).then(() => {
                    copyBtnEl.innerHTML = '<i class="fa-solid fa-check"></i>';
                    setTimeout(() => copyBtnEl.innerHTML = '<i class="fa-regular fa-copy"></i>', 2000);
                });
            });
        }

        // Bind edit button (user messages only)
        const editBtnEl = messageDiv.querySelector('.msg-edit-btn');
        if (editBtnEl && sender === 'user') {
            editBtnEl.addEventListener('click', () => {
                const msgContent = messageDiv.querySelector('.msg-content');
                const originalText = (msgContent?.innerText || '').replace(/Edit$/, '').trim();
                // Replace content with edit form
                const editArea = document.createElement('div');
                editArea.className = 'msg-edit-area';
                editArea.innerHTML = `<textarea>${originalText}</textarea>
                    <div class="msg-edit-actions">
                        <button class="btn btn-secondary btn-cancel-edit">Cancel</button>
                        <button class="btn btn-primary btn-save-edit">Save & Resubmit</button>
                    </div>`;
                // Hide original text, show edit form
                const origContent = msgContent.querySelector('p');
                if (origContent) origContent.style.display = 'none';
                editBtnEl.style.display = 'none';
                msgContent.appendChild(editArea);
                editArea.querySelector('textarea').focus();
                // Cancel
                editArea.querySelector('.btn-cancel-edit').addEventListener('click', () => {
                    editArea.remove();
                    if (origContent) origContent.style.display = '';
                    editBtnEl.style.display = '';
                });
                // Save & Resubmit
                editArea.querySelector('.btn-save-edit').addEventListener('click', async () => {
                    const newText = editArea.querySelector('textarea').value.trim();
                    if (!newText) return;
                    editArea.remove();
                    // Remove this message and all messages after it
                    const allMsgs = [...elements.chatHistory.querySelectorAll('.chat-message')];
                    const msgIdx = allMsgs.indexOf(messageDiv);
                    // Collect database ids of the removed bubbles so the stored
                    // history can be trimmed to match the screen.
                    let staleFromId = null;
                    for (let i = msgIdx; i < allMsgs.length; i++) {
                        const mid = parseInt(allMsgs[i].dataset?.msgId);
                        if (!isNaN(mid) && (staleFromId === null || mid < staleFromId)) {
                            staleFromId = mid;
                        }
                    }
                    for (let i = allMsgs.length - 1; i >= msgIdx; i--) {
                        allMsgs[i].remove();
                    }
                    // Trim stored messages from the edited one onward
                    if (window._currentConvId && staleFromId !== null) {
                        try {
                            await fetch(`/api/conversations/${window._currentConvId}/messages/${staleFromId}`, { method: 'DELETE' });
                        } catch (e) { /* non-critical */ }
                    }
                    // Send the edited question as a new message
                    sendQuery(newText);
                });
            });
        }

        // Bind follow-up chips
        messageDiv.querySelectorAll('.follow-up-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                const q = chip.textContent.trim();
                chip.disabled = true;
                chip.style.opacity = '0.4';
                sendQuery(q);
            });
        });

        elements.chatHistory.appendChild(messageDiv);
        elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
        return messageDiv;
    }
    window.addChatMessage = addChatMessage;

    function addLoadingMessage() {
        const messageDiv = document.createElement("div");
        messageDiv.className = "chat-message system-message loading-placeholder";
        messageDiv.innerHTML = `
            <div class="msg-icon"><i class="fa-solid fa-spinner fa-spin"></i></div>
            <div class="msg-content">
                <p>Orchestrating agents... Coordinator Dispatcher running...</p>
            </div>
        `;
        elements.chatHistory.appendChild(messageDiv);
        elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
        return messageDiv;
    }
    async function sendQuery(queryText) {
        if (!queryText.trim()) return;
        // Duplicate protection: while one follow-up is being processed
        // (Gemini analysis → Groq answer), further sends are ignored so a
        // repeated click/Enter can never fire parallel pipeline runs or
        // create duplicate stored messages.
        if (window._queryInFlight) return;
        window._queryInFlight = true;
        const sendBtn = document.getElementById('sendBtn');
        if (sendBtn) sendBtn.disabled = true;

        addChatMessage("user", `<p>${escapeHtml(queryText)}</p>`);
        elements.chatInput.value = "";

        // Ensure a conversation exists BEFORE sending so the backend can
        // persist this exchange against it (single authoritative save point).
        let convId = null;
        try {
            convId = await ensureConversation();
        } catch (e) { /* unauthenticated or offline: continue without persistence */ }

        if (convId && !window._titleSet) {
            window._titleSet = true;
            autoTitleConversation(convId, queryText);
        }

        // Show loading bubble — reflects the REAL request state and stays
        // until the backend response (or error) actually arrives.
        const loadingBubble = addLoadingMessage();

        try {
            const groqPayload = {
                query: queryText,
                image_path: state.imagePath,
                spatial_data: state.spatialData,
                budget_limit: null,
                conversation_id: window._currentConvId,
                blueprint_id: window._currentBlueprintId,
                plan_duration_months: parseInt(
                    document.getElementById('planDurationMonths')?.value, 10,
                ) || null
            };
            const res = await fetch("/api/query", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(groqPayload)
            });

            // ── Streaming progressive response reader ──────────────────────
            // Read the response body as it arrives and show partial text in
            // the loading bubble so the user gets immediate feedback instead
            // of a blank screen for 10+ seconds.
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let rawChunks = '';

            // Reuse the loading bubble as a live-text container.
            const streamEl = loadingBubble
                ? loadingBubble.querySelector('.msg-content')
                : null;
            if (streamEl) {
                streamEl.innerHTML = '<div class="recommendation-synthesis" id="_streamSink"></div>';
            }
            const sink = document.getElementById('_streamSink');

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true });
                rawChunks += chunk;

                // Live-render growing text so the user sees activity.
                if (sink) {
                    // Extract any plain-text portions from the accumulating
                    // JSON blob so we have something readable mid-stream.
                    const partialText = rawChunks
                        .replace(/[{\["\\]/g, ' ')
                        .replace(/synthesized_recommendation\s*:\s*/gi, '')
                        .replace(/,?\s*"[a-z_]+"\s*:/gi, ' ')
                        .replace(/\s{2,}/g, ' ')
                        .trim()
                        .slice(0, 600);
                    sink.textContent = partialText ? partialText + '…' : '';
                    // Keep scroll locked to bottom during streaming
                    elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
                }
            }

            // Full body is in rawChunks — parse the complete JSON now.
            let data;
            try {
                data = JSON.parse(rawChunks);
            } catch (parseErr) {
                throw new Error('Response parse failed: ' + parseErr.message);
            }

            // Remove loading bubble
            if (loadingBubble && loadingBubble.parentNode) {
                loadingBubble.parentNode.removeChild(loadingBubble);
            }

            if (data.error) {
                addChatMessage("system", `<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> Execution Error: ${data.error}</p>`);
                return;
            }

            // Store for export
            state.lastPipelineResult = data;
            elements.exportReportBtn.style.display = "inline-flex";

            // Append Coordinator Synthesis Markdown
            const formattedRec = formatMarkdown(data.synthesized_recommendation);
            // Generate contextual follow-up suggestions
            const followUps = [];
            const q = queryText.toLowerCase();
            if (q.includes('cost') || q.includes('budget') || q.includes('boq')) {
                followUps.push('Which phase has the highest cost?');
                followUps.push('Can we reduce material costs?');
            } else if (q.includes('compliance') || q.includes('safety') || q.includes('nbc')) {
                followUps.push('What are the main compliance risks?');
                followUps.push('List all fire safety requirements');
            } else if (q.includes('schedule') || q.includes('timeline') || q.includes('phase')) {
                followUps.push('What is the critical path?');
                followUps.push('Can we accelerate Phase 1?');
            } else if (q.includes('design') || q.includes('interior')) {
                followUps.push('Show traditional Indian style');
                followUps.push('What furniture fits the living area?');
            } else {
                followUps.push('Which rooms were detected?');
                followUps.push('Create a cost breakdown by phase');
                followUps.push('Schedule a blueprint review tomorrow');
            }
            const assistantBubble = addChatMessage("system", `
                <div class="recommendation-synthesis">
                    ${formattedRec}
                </div>
            `, followUps);

            // Append Collapsible cards for each specialist output
            appendSpecialistCards(data.specialist_outputs);

            // Structured blueprint-specific worker minimum (from the
            // Gemini-first analysis) — store the requirement for when Calendar is opened.
            const minimumFromRun = data.workforce_minimum?.minimum_workers;
            if (Number.isInteger(minimumFromRun) && minimumFromRun >= 1) {
                window._minWorkers = minimumFromRun;
            }

            // Append Tool Trace Panel
            if (data.tool_execution_trace && data.tool_execution_trace.length > 0) {
                appendToolTracePanel(data.tool_execution_trace);
            }

            // The backend persisted this exchange; stamp both bubbles with
            // their database ids so editing stays consistent with history.
            const userBubbles = elements.chatHistory.querySelectorAll('.chat-message.user-message');
            const lastUserBubble = userBubbles[userBubbles.length - 1];
            if (data.persisted) {
                if (data.persisted.user_message_id && lastUserBubble) {
                    lastUserBubble.dataset.msgId = String(data.persisted.user_message_id);
                }
                if (data.persisted.assistant_message_id && assistantBubble) {
                    assistantBubble.dataset.msgId = String(data.persisted.assistant_message_id);
                }
            }

            // Natural-language calendar event creation from chat input
            checkAndCreateCalendarEvent(queryText);

        } catch (e) {
            if (loadingBubble && loadingBubble.parentNode) {
                loadingBubble.parentNode.removeChild(loadingBubble);
            }
            addChatMessage("system", `<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> Network Error: ${e.message}</p>`);
        } finally {
            // Always release the guard so a failed request never locks the chat.
            window._queryInFlight = false;
            if (sendBtn) sendBtn.disabled = false;
        }
    }

    // Helper to format Markdown from LLM response
    function formatMarkdown(md) {
        if (!md) return "";
        let html = md;

        // Headers
        html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
        html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
        html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');

        // Strong
        html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');

        // Horizontal lines
        html = html.replace(/^\s*---\s*$/gim, '<hr class="md-divider">');

        // Markdown tables → HTML tables (pipes were previously shown as raw text)
        html = html.replace(/(?:^\s*\|.*\|\s*$\n?)+/gim, (block) => {
            const rows = block.trim().split('\n')
                .map(r => r.trim())
                .filter(r => r.startsWith('|') && r.endsWith('|'));
            if (rows.length < 2) return block;
            const isSeparator = (r) => /^\|[\s:|-]+\|$/.test(r);
            const cells = (r) => r.slice(1, -1).split('|').map(c => c.trim());
            const dataRows = rows.filter(r => !isSeparator(r));
            if (!dataRows.length) return block;
            const escapeHtml = (s) => s
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            // Keep already-rendered inline markdown (bold/italic/line breaks),
            // escape everything else.
            const sanitizeCell = (s) => escapeHtml(s)
                .replace(/&lt;(\/?)(strong|em|b|i|br)\s*\/?&gt;/g, '<$1$2>');
            const head = cells(dataRows[0])
                .map(c => `<th>${sanitizeCell(c)}</th>`).join('');
            const body = dataRows.slice(1).map(r =>
                '<tr>' + cells(r).map(c => `<td>${sanitizeCell(c)}</td>`).join('') + '</tr>'
            ).join('');
            return `<div class="md-table-wrap"><table class="md-table">` +
                `<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
        });

        // Bullet Lists
        html = html.replace(/^\s*\*\s+(.*$)/gim, '<li>$1</li>');
        html = html.replace(/^\s*-\s+(.*$)/gim, '<li>$1</li>');
        // Wrap adjacent li in ul
        // A simple parser for lists:
        const lines = html.split('\n');
        let inList = false;
        for (let i = 0; i < lines.length; i++) {
            if (lines[i].startsWith('<li>')) {
                if (!inList) {
                    lines[i] = '<ul>' + lines[i];
                    inList = true;
                }
            } else {
                if (inList) {
                    lines[i - 1] = lines[i - 1] + '</ul>';
                    inList = false;
                }
            }
        }
        if (inList) lines[lines.length - 1] += '</ul>';
        html = lines.join('\n');

        // Paragraphs (wrap blocks of text without html tags)
        html = html.split('\n\n').map(block => {
            const trimmed = block.trim();
            if (!trimmed) return "";
            if (trimmed.startsWith('<h') || trimmed.startsWith('<ul') || trimmed.startsWith('<li') || trimmed.startsWith('<hr') || trimmed.startsWith('<div')) {
                return trimmed;
            }
            return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
        }).join('\n');

        return html;
    }
    window._bsFormatMarkdown = formatMarkdown;

    function appendSpecialistCards(outputs) {
        if (!outputs) return;

        const cardContainer = document.createElement("div");
        cardContainer.className = "specialist-cards-row";

        // 1. Cost estimation card
        if (outputs.cost_estimation) {
            const cost = outputs.cost_estimation;
            let boqRows = cost.boq.map(item => `
                <tr>
                    <td>${item.item}</td>
                    <td>${item.quantity}</td>
                    <td>${item.cost_inr ? `₹${(item.cost_inr / 100000).toFixed(2)}L` : item.rate}</td>
                </tr>
            `).join("");

            createActivityCard(
                "Calculator",
                "Cost Estimation Agent BOQ",
                `
                <table class="boq-table">
                    <thead>
                        <tr>
                            <th>Item Category</th>
                            <th>Quantity</th>
                            <th>Cost (INR)</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${boqRows}
                        <tr>
                            <td><strong>Total Estimate</strong></td>
                            <td>-</td>
                            <td><strong>${cost.formatted_total_cost}</strong></td>
                        </tr>
                    </tbody>
                </table>
                <p style="margin-top: 8px; font-size:11px; color:var(--text-secondary);">${cost.cost_explanation}</p>
                `
            );
        }

        // 2. Compliance checks card
        if (outputs.code_compliance) {
            const comp = outputs.code_compliance;
            let checkItems = comp.compliance_checks.map(item => {
                const iconClass = item.status === "PASS" ? "pass fa-check-circle" : (item.status === "FAIL" ? "fail fa-times-circle" : "warning fa-exclamation-triangle");
                return `
                    <div class="checklist-item">
                        <i class="fa-solid ${iconClass} check-icon"></i>
                        <div class="check-details">
                            <span class="check-title">${item.rule} [${item.status}]</span>
                            <span class="check-message">${item.message}</span>
                            <span class="check-citation">${item.nbc_citation}</span>
                        </div>
                    </div>
                `;
            }).join("");

            createActivityCard(
                "Scale",
                "Code Compliance Agent Checks",
                `
                <div class="compliance-checklist">
                    ${checkItems}
                </div>
                `
            );
        }

        // 3. Scheduling card
        if (outputs.scheduling) {
            const sched = outputs.scheduling;
            let timelineItems = sched.timeline.map(phase => `
                <div class="timeline-item">
                    <div class="timeline-dot"></div>
                    <div class="timeline-details">
                        <span class="timeline-phase">${phase.phase}</span>
                        <span class="timeline-days">${phase.duration_days} days &bull; Milestone: ${phase.milestone}</span>
                    </div>
                </div>
            `).join("");

            createActivityCard(
                "Calendar",
                "Scheduling Agent Timeline",
                `
                <div class="scheduling-timeline" style="margin-bottom:8px;">
                    ${timelineItems}
                </div>
                <p style="font-size:11px; color:var(--text-secondary);"><strong>Total Duration:</strong> ${sched.total_duration_days} Days</p>
                `
            );
        }

        // 4. Workforce matching card
        if (outputs.workforce) {
            const wf = outputs.workforce;
            let wfItems = wf.matches.map(match => {
                const statusClass = match.status === "Available" ? "text-success" : "text-danger";
                return `
                    <div class="workforce-item">
                        <div>
                            <span class="wf-contractor">${match.matched_contractor}</span>
                            <br><span style="font-size:10px; color:var(--text-muted);">${match.trade_category}</span>
                        </div>
                        <div style="text-align:right;">
                            <span class="wf-rate">₹${match.daily_rate_inr}/day</span>
                            <br><span class="wf-status ${statusClass}" style="font-size:10px; font-weight:600;">${match.status}</span>
                        </div>
                    </div>
                    ${match.conflict_details ? `<p style="font-size:10.5px; color:var(--color-orange); margin-top:2px; margin-bottom:6px;">&bull; ${match.conflict_details}</p>` : ''}
                    <hr style="border:none; border-bottom:1px solid rgba(255,255,255,0.03); margin:4px 0;">
                `;
            }).join("");

            createActivityCard(
                "PeopleGroup",
                "Workforce Agent Matches",
                `
                <div class="workforce-list">
                    ${wfItems}
                </div>
                `
            );
        }

        // 5. Interior Design card
        if (outputs.interior_design) {
            const design = outputs.interior_design;
            let roomsHtml = design.rooms.map(room => {
                return `
                    <div style="margin-bottom: 10px;">
                        <h4 style="margin: 0; color: var(--color-blue);">${room.room_name} (${room.function})</h4>
                        <p style="margin: 4px 0; font-size: 11px;"><strong>Colors:</strong> Primary ${room.color_palette.primary}, Accent ${room.color_palette.accent}</p>
                        <p style="margin: 4px 0; font-size: 11px;"><strong>Materials:</strong> Floor: ${room.materials.flooring} | Walls: ${room.materials.walls}</p>
                        <p style="margin: 4px 0; font-size: 11px;"><strong>Lighting:</strong> ${room.lighting}</p>
                        <p style="margin: 4px 0; font-size: 11px;"><strong>Furniture:</strong> ${room.furniture.map(f => f.item).join(', ')}</p>
                    </div>
                `;
            }).join("");

            createActivityCard(
                "Palette",
                "Interior Design Agent",
                `
                <div class="design-recommendations">
                    <p style="font-size:12px; margin-bottom: 10px;"><em>${design.overall_theme}</em></p>
                    ${roomsHtml}
                </div>
                `
            );
        }
    }

    function createActivityCard(iconName, agentTitle, bodyHTML) {
        const cardDiv = document.createElement("div");
        cardDiv.className = "agent-activity-card";

        let faIcon = "fa-cogs";
        if (iconName === "Calculator") faIcon = "fa-calculator";
        if (iconName === "Scale") faIcon = "fa-scale-balanced";
        if (iconName === "Calendar") faIcon = "fa-calendar-days";
        if (iconName === "PeopleGroup") faIcon = "fa-people-group";
        if (iconName === "Palette") faIcon = "fa-palette";

        cardDiv.innerHTML = `
            <div class="activity-header">
                <span><i class="fa-solid ${faIcon}" style="color:var(--color-orange); margin-right:6px;"></i> ${agentTitle}</span>
                <i class="fa-solid fa-chevron-down toggle-arrow"></i>
            </div>
            <div class="activity-body">
                ${bodyHTML}
            </div>
        `;

        // Collapsible binding
        const header = cardDiv.querySelector(".activity-header");
        const body = cardDiv.querySelector(".activity-body");
        const arrow = cardDiv.querySelector(".toggle-arrow");

        header.addEventListener("click", () => {
            body.classList.toggle("collapsed");
            header.classList.toggle("collapsed");
            if (body.classList.contains("collapsed")) {
                arrow.style.transform = "rotate(-90deg)";
            } else {
                arrow.style.transform = "rotate(0deg)";
            }
        });

        elements.chatHistory.appendChild(cardDiv);
        elements.chatHistory.scrollTop = elements.chatHistory.scrollHeight;
    }

    // Input area handlers
    elements.sendBtn.addEventListener("click", () => {
        const text = elements.chatInput.value.trim();
        sendQuery(text);
    });

    elements.chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            const text = elements.chatInput.value.trim();
            sendQuery(text);
        }
    });

    elements.presetQueryBtn.addEventListener("click", () => {
        // Load demo blueprint first if none loaded
        if (!state.spatialData) {
            loadDemoBlueprint().then(() => {
                sendQuery("Can we finish Phase 2 within a ₹15 lakh budget while staying compliant with fire safety norms?");
            });
        } else {
            sendQuery("Can we finish Phase 2 within a ₹15 lakh budget while staying compliant with fire safety norms?");
        }
    });

    elements.clearChatBtn.addEventListener("click", () => {
        // Clear chat items keep welcome
        const welcome = elements.chatHistory.querySelector(".system-message");
        elements.chatHistory.innerHTML = "";
        if (welcome) elements.chatHistory.appendChild(welcome);

        // Reset node visuals
        document.querySelectorAll(".agent-node").forEach(n => {
            if (n.id !== "node-coordinator") n.className = "agent-node node-specialist";
            const badge = n.querySelector(".node-tool-badge");
            if (badge) badge.style.display = "none";
        });
        document.querySelectorAll(".conn-path").forEach(p => p.className.baseVal = "conn-path");
        elements.exportReportBtn.style.display = "none";
    });

    // ----------------------------------------------------
    // Tool Trace Panel & Badges
    // ----------------------------------------------------
    function appendToolTracePanel(trace) {
        if (!trace || trace.length === 0) return;

        let traceHtml = trace.map(call => {
            const rowClass = call.status === "success" ? "success" : "error";
            const time = call.timestamp ? call.timestamp.split("T")[1].substring(0, 8) : "";
            const toolName = call.tool || call.tool_name || "unknown_tool";
            const duration = call.duration_ms ? `${call.duration_ms}ms` : "";

            return `
                <div class="tool-trace-row ${rowClass}">
                    <span><span style="color:#5e697a">[${time}]</span> <span class="tool-trace-name">${toolName}</span></span>
                    <span class="tool-trace-duration">${duration}</span>
                </div>
            `;
        }).join("");

        createActivityCard(
            "Cogs",
            "Tool Execution Trace",
            `<div class="tool-trace-panel">${traceHtml}</div>`
        );
    }

    function updateToolBadges(trace) {
        if (!trace || trace.length === 0) return;

        // Count tool calls heuristically based on tool name
        const counts = {
            "node-cost": 0,
            "node-scheduling": 0,
            "node-compliance": 0,
            "node-coordinator": 0,
            "node-blueprint": 0,
            "node-workforce": 0
        };

        trace.forEach(call => {
            const toolName = call.tool || call.tool_name || "";
            if (toolName.includes("material_price")) counts["node-cost"]++;
            else if (toolName.includes("weather")) counts["node-scheduling"]++;
            else if (toolName.includes("nbc_rule")) counts["node-compliance"]++;
            else counts["node-coordinator"]++;
        });

        for (const [nodeId, count] of Object.entries(counts)) {
            const node = document.getElementById(nodeId);
            if (node) {
                const badge = node.querySelector(".node-tool-badge");
                const countSpan = node.querySelector(".node-tool-badge .count");
                if (badge && countSpan) {
                    if (count > 0) {
                        countSpan.textContent = count;
                        badge.style.display = "block";
                    } else {
                        badge.style.display = "none";
                    }
                }
            }
        }
    }

    elements.exportReportBtn.addEventListener("click", async () => {
        if (!state.lastPipelineResult) {
            alert("No analysis result available to export.");
            return;
        }

        try {
            const res = await fetch("/api/export", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    pipeline_result: state.lastPipelineResult,
                    report_title: "BuildSense Construction Analysis Report"
                })
            });
            const data = await res.json();

            if (data.download_url) {
                window.open(data.download_url, "_blank");
            } else {
                alert("Failed to export report: " + data.error);
            }
        } catch (e) {
            alert("Failed to export report: " + e.message);
        }
    });
});


// --- Enterprise UI Additions --- //

// Tab Navigation
const navBtns = document.querySelectorAll('.nav-btn');
const tabPanes = document.querySelectorAll('.tab-pane');

navBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        navBtns.forEach(b => b.classList.remove('active'));
        tabPanes.forEach(p => p.classList.remove('active'));

        btn.classList.add('active');
        const tabId = btn.getAttribute('data-tab');
        document.getElementById('tab-' + tabId).classList.add('active');

        if (tabId === 'calendar') {
            window._showPlanEvents = false;
            renderCalendar();
            // Phase 1: Workforce/calendar fetch executed on Calendar tab open event
            if (window.parsedBlueprintData || (window.state && window.state.spatialData)) {
                refreshWorkerMinimum();
            }
        }
        if (tabId === 'chats') loadConversations();

    });
});

// Zoom Controls
const zoomInBtn = document.getElementById('zoomInBtn');
const zoomOutBtn = document.getElementById('zoomOutBtn');
if (zoomInBtn) zoomInBtn.addEventListener('click', () => window.setZoom(window._zoomState.level + window._zoomState.step));
if (zoomOutBtn) zoomOutBtn.addEventListener('click', () => window.setZoom(window._zoomState.level - window._zoomState.step));

// Mouse wheel zoom on blueprint (Ctrl+scroll zooms, plain scroll pans)
const scrollArea = document.getElementById('canvasScrollArea');
if (scrollArea) {
    scrollArea.addEventListener('wheel', (e) => {
        if (!window._zoomState || window._zoomState.level <= 0) return;
        if (e.ctrlKey || e.metaKey) {
            e.preventDefault();
            const delta = e.deltaY > 0 ? -0.1 : 0.1;
            window.setZoom(window._zoomState.level + delta);
        }
        // Without Ctrl, allow natural scroll
    }, { passive: false });

    // Drag-to-pan when zoomed (scroll-based)
    scrollArea.addEventListener('mousedown', (e) => {
        const zs = window._zoomState;
        if (zs.level <= 1) return;
        zs.isPanning = true;
        zs.startPanX = e.clientX + scrollArea.scrollLeft;
        zs.startPanY = e.clientY + scrollArea.scrollTop;
        scrollArea.style.cursor = 'grabbing';
        e.preventDefault();
    });
    window.addEventListener('mousemove', (e) => {
        const zs = window._zoomState;
        if (!zs || !zs.isPanning) return;
        scrollArea.scrollLeft = zs.startPanX - e.clientX;
        scrollArea.scrollTop = zs.startPanY - e.clientY;
    });
    window.addEventListener('mouseup', () => {
        const zs = window._zoomState;
        if (zs) zs.isPanning = false;
        if (scrollArea) scrollArea.style.cursor = '';
    });
}

// Image Downscaling / Compression Helper for Payload Optimization
function downscaleCanvasImage(canvas, maxDim = 800, quality = 0.6) {
    if (!canvas || !canvas.width || !canvas.height) return null;
    try {
        let width = canvas.width;
        let height = canvas.height;
        if (width > maxDim || height > maxDim) {
            if (width > height) {
                height = Math.round((height * maxDim) / width);
                width = maxDim;
            } else {
                width = Math.round((width * maxDim) / height);
                height = maxDim;
            }
        }
        const offCanvas = document.createElement('canvas');
        offCanvas.width = width;
        offCanvas.height = height;
        const ctx = offCanvas.getContext('2d');
        ctx.drawImage(canvas, 0, 0, width, height);
        return offCanvas.toDataURL('image/jpeg', quality);
    } catch (e) {
        console.warn('[BuildSense] Image downscaling skipped:', e);
        return null;
    }
}

// Helper to safely extract interior design data from Groq, Gemini, or Coordinator outputs
function extractInteriorDesignData(data) {
    if (!data || typeof data !== 'object') return null;

    // 1. Safe optional-chaining fallback for object structures
    const designObj = data?.specialist_outputs?.interior_design || data?.interior_design || data?.design;
    if (designObj && typeof designObj === 'object') {
        return designObj;
    }

    // 2. Safe property checks following fallback chain:
    // data?.specialist_outputs?.interior_design || data?.answer || data?.response || data?.choices?.[0]?.message?.content || JSON.stringify(data)
    const rawText = (typeof data?.specialist_outputs?.interior_design === 'string' ? data.specialist_outputs.interior_design : null)
                 || data?.answer
                 || data?.response
                 || data?.text
                 || data?.choices?.[0]?.message?.content
                 || data?.candidates?.[0]?.content
                 || data?.synthesized_recommendation;

    if (typeof rawText === 'string') {
        try {
            const jsonMatch = rawText.match(/\{[\s\S]*\}/);
            if (jsonMatch) {
                return JSON.parse(jsonMatch[0]);
            }
        } catch (e) {
            console.warn('[BuildSense Design Studio] Could not parse JSON from raw text:', e);
        }
    }
    return null;
}

// ---------------------------------------------------------------------------
// Groq API Key Rotation (Round-Robin) & Model Configuration
// ---------------------------------------------------------------------------
const groqApiKeys = [
    'YOUR_KEY_1',
    'YOUR_KEY_2',
    'YOUR_KEY_3',
    'YOUR_KEY_4'
];
let currentKeyIndex = 0;

function getNextApiKey() {
    const key = groqApiKeys[currentKeyIndex];
    currentKeyIndex = (currentKeyIndex + 1) % groqApiKeys.length;
    return key;
}

// Generate Design
const genDesignBtn = document.getElementById('generateDesignBtn');
if(genDesignBtn) {
    genDesignBtn.addEventListener('click', async () => {
        // Duplicate protection: ignore extra clicks while a generation request is in flight
        if (window._designInFlight) {
            console.warn('[BuildSense Design Studio] ⚠️ Ignoring click — request already in flight.');
            return;
        }
        window._designSeq = (window._designSeq || 0) + 1;
        const seq = window._designSeq;

        const preset = document.getElementById('stylePresetSelector')?.value || 'modern_minimalist';
        const styleName = preset.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
        const designContainer = document.getElementById('designContainer');
        const originalBtnHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Generate Design';

        // IDLE -> GENERATING
        if (designContainer) {
            designContainer.innerHTML = '<div style="text-align:center;width:100%;padding:40px 0;"><i class="fa-solid fa-spinner fa-spin fa-2x" style="color:var(--color-blue);"></i><p style="margin-top:12px;color:var(--text-secondary);">Analysing rooms and generating design plan…</p></div>';
        }
        genDesignBtn.disabled = true;
        genDesignBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Generating… ⏳';

        const designAbortCtrl = new AbortController();
        const designTimeoutId = setTimeout(() => designAbortCtrl.abort(), 45_000);

        try {
            const rawSpatialData = window.parsedBlueprintData || window.currentSpatialData || (window.state && window.state.spatialData) || null;
            const imagePath = window.currentImagePath || (window.parsedBlueprintData && window.parsedBlueprintData.image_path) || "";

            const bpCanvas = document.getElementById('blueprintCanvas');
            let compressedThumbnail = null;
            try {
                compressedThumbnail = bpCanvas ? downscaleCanvasImage(bpCanvas, 600, 0.5) : null;
            } catch (thumbErr) {
                console.warn('[BuildSense Design Studio] ⚠️ Thumbnail compression failed (non-fatal):', thumbErr.message);
            }

            let apiKey = 'none';
            try {
                apiKey = getNextApiKey();
            } catch (keyErr) {
                console.warn('[BuildSense Design Studio] ⚠️ getNextApiKey() failed (non-fatal):', keyErr.message);
            }

            const fetchPayload = {
                query: `Generate detailed, custom interior design recommendations for each room in this blueprint using ${styleName} (${preset}) style. Provide distinct furniture items, color palettes, and material selections for every room.`,
                image_path: imagePath,
                spatial_data: rawSpatialData,
                image_base64: compressedThumbnail || null,
                blueprint_id: window._currentBlueprintId || null,
                style_preset: preset,
                conversation_id: window._currentConvId || null
            };

            const res = await fetch('/api/query', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                signal: designAbortCtrl.signal,
                body: JSON.stringify(fetchPayload)
            });
            clearTimeout(designTimeoutId);
            if (seq !== window._designSeq) return; // superseded mid-flight

            // Phase 3: Catch HTTP Errors Immediately without waiting for timeout
            if (!res.ok) {
                let errText = `HTTP ${res.status} (${res.statusText || 'Error'})`;
                try {
                    const errJson = await res.json();
                    if (errJson && errJson.error) errText = errJson.error;
                } catch (e) {
                    const txt = await res.text().catch(() => '');
                    if (txt) errText = txt.slice(0, 200);
                }
                throw new Error(errText);
            }

            const data = await res.json();

            if (data?.error) {
                if (designContainer) {
                    designContainer.innerHTML = `<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> Design generation failed: ${escapeHtml(data.error)}</p>`;
                }
                return;
            }

            // Phase 2: Safe, bulletproof fallback chain for text extraction
            let designText = "Could not parse response.";
            if (data) {
                designText = data?.specialist_outputs?.interior_design 
                          || data?.answer 
                          || data?.response 
                          || data?.choices?.[0]?.message?.content 
                          || data?.text
                          || JSON.stringify(data);
            }

            // Phase 2: DOM Injection wrapped in try...catch block
            try {
                let designObj = null;
                if (typeof designText === 'object' && designText !== null) {
                    designObj = designText;
                } else if (typeof designText === 'string') {
                    try {
                        const jsonMatch = designText.match(/\{[\s\S]*\}/);
                        if (jsonMatch) {
                            designObj = JSON.parse(jsonMatch[0]);
                        }
                    } catch (parseErr) {
                        console.warn('[BuildSense Design Studio] Could not parse JSON from designText:', parseErr);
                    }
                }

                const design = designObj || extractInteriorDesignData(data);

                if (design && designContainer && Array.isArray(design.rooms) && design.rooms.length > 0) {
                    const overallTheme = escapeHtml(design.overall_theme || design.theme || 'Cohesive design concept tailored to your spatial layout.');
                    const rooms = design.rooms;

                    let html = `
                        <div style="grid-column: 1 / -1; margin-bottom: 15px;">
                            <h3>Design Theme:</h3>
                            <p style="color:var(--text-secondary);">${overallTheme}</p>
                        </div>
                    `;

                    rooms.forEach(room => {
                        const roomName = escapeHtml(room?.room_name || room?.name || 'Room');
                        const roomFunc = escapeHtml(room?.function || room?.room_type || 'General Space');
                        const primaryColor = room?.color_palette?.primary || '#00d2ff';
                        const accentColor = room?.color_palette?.accent || '#ff9500';
                        const flooring = escapeHtml(room?.materials?.flooring || 'Vitrified / Premium Flooring');
                        const walls = escapeHtml(room?.materials?.walls || 'Emulsion Paint with Accent Texture');
                        const lighting = escapeHtml(room?.lighting || 'Ambient & Accent Lighting');

                        let furnItems = '';
                        if (Array.isArray(room?.furniture)) {
                            furnItems = room.furniture.map(f => {
                                if (typeof f === 'string') {
                                    return `<li><i class="fa-solid fa-check"></i> ${escapeHtml(f)}</li>`;
                                }
                                const item = escapeHtml(f?.item || 'Furniture Item');
                                const placement = escapeHtml(f?.placement || 'Standard Layout');
                                return `<li><i class="fa-solid fa-check"></i> ${item} <br><small style="color:var(--text-muted)">${placement}</small></li>`;
                            }).join('');
                        }

                        html += `
                            <div class="room-card">
                                <h3>${roomName}</h3>
                                <span class="control-badge badge-blue" style="width:fit-content">${roomFunc}</span>
                                <div class="color-swatches">
                                    <div class="swatch" style="background-color: ${primaryColor};" title="Primary: ${primaryColor}"></div>
                                    <div class="swatch" style="background-color: ${accentColor};" title="Accent: ${accentColor}"></div>
                                </div>
                                <p style="font-size:12px"><strong>Flooring:</strong> ${flooring}</p>
                                <p style="font-size:12px"><strong>Walls:</strong> ${walls}</p>
                                <p style="font-size:12px"><strong>Lighting:</strong> ${lighting}</p>
                                <h4 style="margin-top:10px; font-size:13px; color:var(--text-secondary)">Furniture:</h4>
                                <ul class="furniture-list">${furnItems || '<li>Room-appropriate furniture recommendation</li>'}</ul>
                            </div>
                        `;
                    });

                    designContainer.innerHTML = html;
                } else if (designContainer) {
                    // Fallback to text render if rooms array is not present
                    const displayOutput = typeof designText === 'string' ? designText : JSON.stringify(designText, null, 2);
                    designContainer.innerHTML = `
                        <div style="grid-column: 1 / -1; background: var(--bg-card, rgba(255,255,255,0.03)); border: 1px solid var(--border-glass, rgba(255,255,255,0.1)); border-radius: 8px; padding: 18px;">
                            <h3 style="margin-bottom: 10px; color: var(--color-blue);"><i class="fa-solid fa-palette"></i> Interior Design Plan</h3>
                            <div style="color: var(--text-primary); line-height: 1.6; white-space: pre-wrap;">${escapeHtml(displayOutput)}</div>
                        </div>
                    `;
                }
            } catch (domErr) {
                console.error("DOM Injection Failed:", domErr);
                if (designContainer) {
                    designContainer.innerHTML = `<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> Error displaying design: ${escapeHtml(domErr.message)}</p>`;
                }
            }
        } catch (e) {
            clearTimeout(designTimeoutId);
            console.error("Design Generation Failed:", e);
            if (seq === window._designSeq && designContainer) {
                const isTimeout = (e.name === 'AbortError');
                const msg = isTimeout
                    ? 'The design request timed out (45 s). The AI model may be busy — please try again in a moment.'
                    : escapeHtml(e.message);
                designContainer.innerHTML = `<p class="text-danger"><p style="margin-top:6px;"><i class="fa-solid fa-circle-exclamation"></i> ${msg}</p>`;
            }
        } finally {
            clearTimeout(designTimeoutId);
            // Phase 2: GUARANTEED UNFREEZE IN FINALLY BLOCK — always restores button state & clears in-flight flag
            if (seq === window._designSeq && genDesignBtn) {
                genDesignBtn.disabled = false;
                genDesignBtn.innerHTML = originalBtnHTML;
            }
            window._designInFlight = false;
        }
    });
}

// A newly selected chat must never keep the previous chat's design result.
function resetDesignStudioState() {
    window._designInFlight = false;
    window._designSeq = (window._designSeq || 0) + 1; // invalidate in-flight renders
    const container = document.getElementById('designContainer');
    const btn = document.getElementById('generateDesignBtn');
    if (container) container.innerHTML = '';
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Generate Design'; }
}

// ── Date-aware monthly calendar + generated construction plan ──
// The visible month is pure view state derived from the REAL system date —
// never a hardcoded or input-coupled value — so prev/next navigation and
// multi-month plans stay attached to the correct dates.

const CAL_DAY_HEADERS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function _fmtISO(d) {
    // Local-time YYYY-MM-DD (toISOString would shift by timezone).
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

(function initCalendarView() {
    const now = new Date();
    window._calView = { y: now.getFullYear(), m: now.getMonth() }; // 0-indexed
})();

function _planDateLabel(iso) {
    const d = new Date(`${iso}T00:00:00`);
    return d.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
}

function showPlanMessage(msg, isError = false) {
    const el = document.getElementById('planMessage');
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('error', isError);
    el.classList.toggle('success', !isError);
    el.hidden = false;
}

function clearPlanMessage() {
    const el = document.getElementById('planMessage');
    if (el) el.hidden = true;
}

function _prefillDurationInputs() {
    const startInput = document.getElementById('planStartDate');
    if (!startInput) return;
    const today = new Date();
    if (!startInput.value) startInput.value = _fmtISO(today);
}

// Calendar-accurate month addition (never a fixed 30-day month): handles
// different month lengths, February, leap years, year transitions, and
// clamps month-end starts to the target month's last day (31 Jan + 1M -> Feb).
function addMonthsClamped(startDate, months) {
    const totalMonthIndex = startDate.getMonth() + months;
    const targetYear = startDate.getFullYear() + Math.floor(totalMonthIndex / 12);
    const targetMonth = ((totalMonthIndex % 12) + 12) % 12;
    const daysInTargetMonth = new Date(targetYear, targetMonth + 1, 0).getDate();
    const targetDay = Math.min(startDate.getDate(), daysInTargetMonth);
    return new Date(targetYear, targetMonth, targetDay);
}

function _fmtISODate(dateObj) {
    return _fmtISO(dateObj);
}

// ── Duration-specific minimum workers (structured values derived from the
// Gemini-first blueprint analysis — never parsed from Groq's chat text).
// The SAME project needs a different minimum crew per selected duration, so
// every duration (or start date) change triggers a fresh calculation. ──
window._showPlanEvents = false;
window._minWorkers = null;
window._wfReqSeq = 0;      // request identity: stale responses never apply
window._wfPending = false; // recalculation in flight → block plan generation
window._wfInfeasible = false;
window._wfInfeasibleReason = '';

function _wfElements() {
    return {
        hint: document.getElementById('workersHint'),
        input: document.getElementById('planWorkers'),
        btn: document.getElementById('generatePlanBtn'),
        durSel: document.getElementById('planDurationMonths'),
    };
}

function calculateMinimumWorkersForArea(totalAreaSqFt, durationMonths) {
    const months = parseInt(durationMonths, 10) || 3;
    const workingDays = months * 26; // 26 working days per month (6-day work week)
    const availableWorkingHours = workingDays * 8; // 8 hours per day
    const area = (typeof totalAreaSqFt === 'number' && totalAreaSqFt > 0) ? totalAreaSqFt : 500;

    // Labor factor: ~1.8 man-hours per sq ft for total construction/renovation scope
    const totalManHours = area * 1.8;
    const calculatedWorkers = Math.ceil(totalManHours / availableWorkingHours);

    // Floor constraint: Never less than 3 workers for any structural construction project
    return Math.max(3, calculatedWorkers);
}

function validateWorkersInput() {
    const { hint, input, btn, durSel } = _wfElements();
    if (!input || !hint) return true;

    if (window._wfInfeasible) {
        const infeasibleMsg = window._wfInfeasibleReason || 'The selected duration is not realistically achievable for this project.';
        hint.textContent = `⚠️ ${infeasibleMsg}`;
        hint.classList.add('error');
        hint.hidden = false;
        showPlanMessage(infeasibleMsg, true);
        if (btn) btn.disabled = true;
        return false;
    }

    const valRaw = input.value.trim();
    if (!valRaw) return true;
    const val = parseInt(valRaw, 10);
    const min = Math.max(3, window._minWorkers || 3);
    const months = parseInt(durSel?.value, 10) || 3;

    if (Number.isInteger(val) && val < min) {
        const errorMsg = `You need at least ${min} workers to complete the project in ${months} months. Please increase the number of workers or extend the time duration.`;
        hint.textContent = `⚠️ ${errorMsg}`;
        hint.classList.add('error');
        hint.hidden = false;
        showPlanMessage(errorMsg, true);
        if (btn) btn.disabled = true;
        return false;
    }

    hint.textContent = `✅ Ready to generate plan with ${val} workers.`;
    hint.classList.remove('error');
    hint.hidden = false;
    clearPlanMessage();
    if (btn && !window._wfPending) btn.disabled = false;
    return true;
}

function _applyWorkerMinimum(data, months) {
    const { hint, input, btn } = _wfElements();
    window._wfInfeasible = false;
    window._wfInfeasibleReason = '';

    if (!hint || !input) return;

    if (data.feasible === false) {
        window._minWorkers = null;
        window._wfInfeasible = true;
        window._wfInfeasibleReason = data.reason
            || `The selected ${months}-month duration may not be realistically achievable for this project because of construction task dependencies.`;
        input.removeAttribute('min');
        hint.textContent = `⚠️ ${window._wfInfeasibleReason}`;
        hint.classList.add('error');
        hint.hidden = false;
        if (btn) btn.disabled = true;
        return;
    }

    if (btn) btn.disabled = false;

    let minimum = Number.isInteger(data.minimum_workers) && data.minimum_workers >= 1
        ? data.minimum_workers : null;

    // Area-based calculation from blueprint context if available
    const bpData = window.parsedBlueprintData || (window.state && window.state.spatialData);
    const totalArea = bpData?.total_area_sqft || (bpData?.rooms ? bpData.rooms.reduce((acc, r) => acc + (r.area_sqft || 0), 0) : 0);
    const calculatedAreaWorkers = calculateMinimumWorkersForArea(totalArea, months);

    minimum = Math.max(3, minimum || calculatedAreaWorkers);
    window._minWorkers = minimum;

    hint.textContent = `Minimum required to complete this project within ${months} month(s): ${minimum} workers`;
    hint.classList.remove('error');
    hint.hidden = false;
    input.min = minimum;

    // Instantly auto-fill input with calculated minimum (if empty or less than minimum)
    const current = parseInt(input.value, 10);
    if (!Number.isFinite(current) || current < minimum) {
        input.value = String(minimum);
    }
    validateWorkersInput();
}

async function refreshWorkerMinimum() {
    const { hint, input, btn, durSel } = _wfElements();
    const months = parseInt(durSel?.value, 10);
    const seq = ++window._wfReqSeq;

    // Real processing state while the duration-specific requirement is
    // being calculated — generation stays blocked until it resolves.
    window._wfPending = true;
    if (btn) btn.disabled = true;
    if (hint) {
        hint.textContent = 'Calculating workforce requirement...';
        hint.classList.remove('error');
        hint.hidden = false;
    }

    try {
        const qs = new URLSearchParams();
        if (window._currentConvId) qs.set('conversation_id', window._currentConvId);
        if (Number.isInteger(months) && months >= 1 && months <= 12) {
            qs.set('duration_months', String(months));
            const startVal = document.getElementById('planStartDate')?.value.trim();
            if (startVal) qs.set('start_date', startVal);
        }
        const res = await fetch(`/api/calendar/workforce-minimum?${qs.toString()}`);
        if (res.status === 401) return;
        const data = await res.json().catch(() => ({}));
        // Race protection: only the response for the CURRENTLY selected
        // duration (latest request) may update the UI.
        const currentMonths = parseInt(durSel?.value, 10);
        if (seq !== window._wfReqSeq || currentMonths !== months) return;
        window._wfPending = false;
        if (btn && !window._wfInfeasible) btn.disabled = false;
        _applyWorkerMinimum(data, months);
    } catch (e) {
        console.error('Worker minimum fetch failed:', e);
        if (seq !== window._wfReqSeq) return;
        window._wfPending = false;
        if (btn) btn.disabled = false;
        if (hint) {
            hint.textContent = 'Could not verify the workforce requirement for this duration. Please retry.';
            hint.classList.add('error');
            hint.hidden = false;
        }
    }
}

function resetWorkerMinimumContext() {
    window._showPlanEvents = false;
    // Invalidate any in-flight recalculation from the previous chat.
    window._wfReqSeq += 1;
    window._wfPending = false;
    window._wfInfeasible = false;
    window._wfInfeasibleReason = '';
    window._minWorkers = null;
    const { hint, input, btn } = _wfElements();
    if (btn) btn.disabled = false;
    if (hint) { hint.hidden = true; hint.textContent = ''; hint.classList.remove('error'); }
    if (input) { input.removeAttribute('min'); }
}

function _fmtDayMonthYear(iso) {
    return new Date(`${iso}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
}

function renderConstructionPlanPanel(eventsByDate, highlightDate = null) {
    const list = document.getElementById('planList');
    const emptyState = document.getElementById('planEmptyState');
    if (!list || !emptyState) return;

    const planRows = [];
    Object.entries(eventsByDate).forEach(([date, evts]) => {
        evts.forEach(event => {
            if (event.category === 'construction_plan') {
                planRows.push({ date, title: event.title || 'Construction work', description: event.description || '', location: event.location || '' });
            }
        });
    });
    planRows.sort((a, b) => a.date.localeCompare(b.date));

    if (!planRows.length) {
        emptyState.hidden = false;
        list.replaceChildren();
        return;
    }
    emptyState.hidden = true;

    // Display-level grouping ONLY: consecutive calendar dates carrying the
    // SAME activity collapse into ONE timeline block ("23 August 2026 → 27
    // August 2026"). The underlying schedule rows are never modified, runs
    // stay strictly chronological, and a different (or non-consecutive) day
    // always starts a new block.
    const DAY_MS = 86400000;
    const blocks = [];
    planRows.forEach(row => {
        const last = blocks[blocks.length - 1];
        if (last && last.title === row.title) {
            const gap = (new Date(`${row.date}T00:00:00`) - new Date(`${last.endDate}T00:00:00`)) / DAY_MS;
            if (gap === 1) {
                last.endDate = row.date;
                return;
            }
        }
        blocks.push({ startDate: row.date, endDate: row.date, title: row.title, location: row.location, description: row.description });
    });

    list.replaceChildren();
    blocks.forEach(block => {
        const group = document.createElement('div');
        group.className = 'plan-group';
        group.dataset.planStart = block.startDate;
        group.dataset.planEnd = block.endDate;

        const dateEl = document.createElement('div');
        dateEl.className = 'plan-date';
        dateEl.textContent = (block.startDate === block.endDate)
            ? _planDateLabel(block.startDate)
            : `${_fmtDayMonthYear(block.startDate)} \u2192 ${_fmtDayMonthYear(block.endDate)}`;
        group.appendChild(dateEl);

        const dStart = new Date(`${block.startDate}T00:00:00`);
        const dEnd = new Date(`${block.endDate}T00:00:00`);
        const durDays = Math.max(1, Math.round((dEnd - dStart) / DAY_MS) + 1);
        const durationSuffix = durDays > 1 ? ` - ${durDays} days` : ' - 1 day';

        const activity = document.createElement('div');
        activity.className = 'plan-activity';
        activity.textContent = `${block.title}${durationSuffix}`;
        group.appendChild(activity);
        if (block.location) {
            const loc = document.createElement('div');
            loc.className = 'plan-location';
            loc.textContent = `Location:\n${block.location}`;
            group.appendChild(loc);
        }
        if (block.description) {
            const desc = document.createElement('div');
            desc.className = 'plan-desc';
            // Location is shown on its own line above; don't repeat it here.
            desc.textContent = block.description.replace(/^Location:\s*[^|]*\|\s*/, '');
            group.appendChild(desc);
        }
        list.appendChild(group);
    });

    if (highlightDate) {
        const target = Array.from(list.querySelectorAll('.plan-group'))
            .find(g => g.dataset.planStart <= highlightDate && highlightDate <= g.dataset.planEnd);
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
            target.classList.add('flash');
            setTimeout(() => target.classList.remove('flash'), 1600);
        }
    }
}

async function renderCalendar(highlightPlanDate = null) {
    const grid = document.getElementById('calendarGrid');
    if (!grid) return;

    // Render month view & events for current calendar state

    let events = [];
    let holidays = [];

    try {
        if (window._showPlanEvents) {
            const qs = window._currentConvId ? '?conversation_id=' + window._currentConvId : '';
            const eventsResponse = await fetch('/api/calendar/events' + qs);
            if (eventsResponse.ok) {
                const eventsPayload = await eventsResponse.json();
                events = Array.isArray(eventsPayload.events) ? eventsPayload.events : [];
                const calendarData = state.lastPipelineResult?.specialist_outputs?.calendar || {};
                holidays = Array.isArray(calendarData.holidays) ? calendarData.holidays : [];
            }
        }

        const visibleForRole = (event) => {
            const assignment = event.assignment || {};
            const contractorId = assignment.contractor_id || event.contractor_id;
            const coworkers = assignment.coworkers || event.coworkers || [];
            if (state.currentUser?.role === 'contractor') return state.currentUser.id === contractorId;
            if (state.currentUser?.role === 'coworker') {
                return coworkers.some(worker => String(worker.name || '').toLowerCase() === String(state.currentUser.id || '').toLowerCase());
            }
            return true;
        };
        const eventDates = (event) => {
            const daily = Array.isArray(event.daily_schedule) ? event.daily_schedule : [];
            if (daily.length) return daily.map(item => item.date).filter(Boolean);
            return [event.date].filter(Boolean);
        };
        const eventsByDate = {};
        events.filter(visibleForRole).forEach(event => {
            eventDates(event).forEach(date => (eventsByDate[date] ||= []).push(event));
        });

        renderConstructionPlanPanel(eventsByDate, highlightPlanDate);

        const year = window._calView.y;
        const month = window._calView.m;
        const todayIso = _fmtISO(new Date());

        grid.replaceChildren();
        CAL_DAY_HEADERS.forEach(day => {
            const header = document.createElement('div');
            header.className = 'day-header';
            header.textContent = day;
            grid.appendChild(header);
        });

        const firstDayOffset = new Date(year, month, 1).getDay(); // 0 = Sunday
        for (let i = 0; i < firstDayOffset; i++) {
            const emptyCell = document.createElement('div');
            emptyCell.className = 'day-cell other-month';
            grid.appendChild(emptyCell);
        }

        const daysInMonth = new Date(year, month + 1, 0).getDate();

        for (let i = 1; i <= daysInMonth; i++) {
            const cellDateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(i).padStart(2, '0')}`;
            const curDate = new Date(year, month, i);
            const dayOfWeek = curDate.getDay(); // 0=Sunday

            const cell = document.createElement('div');
            cell.className = 'day-cell';
            cell.dataset.date = cellDateStr;

            const number = document.createElement('div');
            number.className = 'day-number';
            number.textContent = i;
            cell.appendChild(number);

            if (cellDateStr === todayIso) cell.classList.add('today');

            const hasEvents = (eventsByDate[cellDateStr] || []).length > 0;
            if (hasEvents) {
                cell.classList.add('has-events');
                const dot = document.createElement('span');
                dot.className = 'event-dot';
                dot.title = `${eventsByDate[cellDateStr].length} scheduled item(s)`;
                number.appendChild(dot);
            }

            // Check holidays
            const holiday = holidays.find(item => item.date === cellDateStr);
            if (holiday || dayOfWeek === 0) {
                cell.classList.add('holiday');
                const off = document.createElement('div');
                off.className = 'event-bar off-day';
                off.style.background = holiday ? 'var(--color-red)' : 'var(--text-muted)';
                off.style.color = '#fff';
                off.textContent = holiday?.name || 'Sunday Off';
                off.title = off.textContent;
                cell.appendChild(off);
            }

            if (hasEvents) {
                const dayEvents = eventsByDate[cellDateStr];
                // Calendar cells carry ONLY a short activity chip for quick
                // visual identification — full details live in the right-side
                // construction plan panel.
                const MAX_CHIPS = 2;
                dayEvents.slice(0, MAX_CHIPS).forEach(event => {
                    const assignment = event.assignment || {};
                    const crew = assignment.contractor_name || event.contractor || event.project_context || '';
                    const timeLabel = (event.start_time && event.end_time)
                        ? ` · ${event.start_time}–${event.end_time}`
                        : '';
                    const chip = document.createElement('div');
                    chip.className = 'event-bar';
                    chip.textContent = event.title || 'Project phase';
                    if (crew || timeLabel) chip.title = `${chip.textContent}${timeLabel}${crew ? ' · ' + crew : ''}`;
                    cell.appendChild(chip);
                });
                if (dayEvents.length > MAX_CHIPS) {
                    const more = document.createElement('div');
                    more.className = 'event-bar more-chip';
                    more.textContent = `+${dayEvents.length - MAX_CHIPS} more`;
                    more.title = dayEvents.slice(MAX_CHIPS).map(e => e.title || 'Project phase').join(', ');
                    cell.appendChild(more);
                }

                // Clicking a scheduled date surfaces its activities in the plan panel.
                cell.style.cursor = 'pointer';
                cell.addEventListener('click', () => {
                    grid.querySelectorAll('.day-cell.selected').forEach(c => c.classList.remove('selected'));
                    cell.classList.add('selected');
                    renderConstructionPlanPanel(eventsByDate, cellDateStr);
                });
            }

            grid.appendChild(cell);
        }
        document.getElementById('calMonthYear').textContent =
            new Date(year, month, 1).toLocaleString('default', { month: 'long', year: 'numeric' });

    } catch (e) {
        console.error("Calendar load failed", e);
        grid.replaceChildren();
        const errCell = document.createElement('div');
        errCell.className = 'day-header';
        errCell.style.gridColumn = '1 / -1';
        errCell.style.color = 'var(--color-red, #ff6b6b)';
        errCell.textContent = `Calendar failed to load: ${e.message}`;
        grid.appendChild(errCell);
    }
}

document.getElementById('generatePlanBtn')?.addEventListener('click', async () => {
    const btn = document.getElementById('generatePlanBtn');
    const startVal = document.getElementById('planStartDate')?.value.trim();
    const monthsRaw = document.getElementById('planDurationMonths')?.value;
    const workersRaw = document.getElementById('planWorkers')?.value.trim();
    clearPlanMessage();

    if (!startVal) { showPlanMessage('Please select a start date.', true); return; }

    const startDate = new Date(`${startVal}T00:00:00`);
    if (isNaN(startDate.getTime())) { showPlanMessage('The start date is invalid.', true); return; }

    // Duration — dropdown only, exactly 1-12 months (no arbitrary values).
    const durationMonths = parseInt(monthsRaw, 10);
    if (!Number.isInteger(durationMonths) || durationMonths < 1 || durationMonths > 12) {
        showPlanMessage('Please select a duration between 1 and 12 months.', true);
        return;
    }

    // Workers — whole positive numbers only.
    if (!/^\d+$/.test(workersRaw || '')) {
        showPlanMessage('Please enter a valid number of workers (a whole number of at least 1).', true);
        return;
    }
    const workers = parseInt(workersRaw, 10);
    if (workers < 1) {
        showPlanMessage('Workers must be a whole number of at least 1.', true);
        return;
    }

    // Enforce the duration-specific structured minimum BEFORE generating.
    // A recalculation still in flight blocks generation so an outdated
    // minimum can never be used.
    if (window._wfPending) {
        showPlanMessage('Still calculating the workforce requirement for this duration — please wait a moment.', true);
        return;
    }
    if (window._wfInfeasible) {
        showPlanMessage(
            window._wfInfeasibleReason
            || 'The selected duration is not realistically achievable for this project.',
            true,
        );
        return;
    }
    if (window._minWorkers && workers < window._minWorkers) {
        const hint = document.getElementById('workersHint');
        if (hint) {
            hint.textContent = `Minimum required to complete this project within ${durationMonths} month(s): ${window._minWorkers} workers`;
            hint.classList.add('error');
            hint.hidden = false;
        }
        showPlanMessage(
            `This project requires at least ${window._minWorkers} workers to meet the selected ${durationMonths}-month duration.`,
            true,
        );
        return;
    }

    // End date derived internally from Start Date + Duration (calendar math).
    const endDate = addMonthsClamped(startDate, durationMonths);

    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Generating plan...';
    try {
        const res = await fetch('/api/calendar/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                start_date: startVal,
                duration_months: durationMonths,
                workers,
                conversation_id: window._currentConvId,
            }),
        });
        if (res.status === 401) {
            // Session expired server-side — send the user to log in again
            // instead of surfacing a raw auth error.
            window.location.href = '/login';
            return;
        }
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
            const err = payload.error || 'Unable to generate the construction plan. Please try again.';
            showPlanMessage(err, true);
            // A backend rejection may carry a refreshed duration-specific
            // requirement — sync the hint and input minimum immediately.
            if (payload.feasible === false && typeof payload.error === 'string') {
                window._wfInfeasible = true;
                window._wfInfeasibleReason = payload.error;
                const { hint, btn } = _wfElements();
                window._minWorkers = null;
                if (hint) {
                    hint.textContent = `⚠️ ${payload.error}`;
                    hint.classList.add('error');
                    hint.hidden = false;
                }
                if (btn) btn.disabled = true;
            } else if (Number.isInteger(payload.minimum_workers) && Number.isInteger(payload.duration_months)) {
                _applyWorkerMinimum({
                    feasible: true,
                    minimum_workers: payload.minimum_workers,
                }, payload.duration_months);
            }
            return;
        }
        // Phase 4: Enable events ONLY after successful generation
        window._showPlanEvents = true;
        const newStart = new Date(`${startVal}T00:00:00`);
        window._calView = { y: newStart.getFullYear(), m: newStart.getMonth() };
        await renderCalendar(payload.plan_start || startVal);
        // Duration-feasibility feedback: compressed or overrunning schedules
        // are flagged by the scheduling engine and must be shown honestly.
        const feas = payload.feasibility;
        let planMsg = `Construction plan generated — ${payload.total_days} day(s), ${payload.working_days} working day(s), ${payload.workers ?? workers} worker(s) over ${payload.duration_months ?? durationMonths} month(s).`;
        let isWarning = false;
        if (feas && typeof feas.message === 'string' && feas.status && feas.status !== 'ok') {
            planMsg = `${feas.message}`;
            isWarning = feas.status === 'tight' ? false : true;
            if (feas.status !== 'tight') {
                planMsg = `${feas.message} Plan generated for ${payload.duration_months ?? durationMonths} month(s) with ${payload.working_days} working day(s).`;
            }
        }
        showPlanMessage(planMsg, isWarning);
    } catch (e) {
        console.error('Generate plan failed:', e);
        showPlanMessage('Unable to generate the construction plan. Please try again.', true);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Generate Plan';
    }
});

// Changing the duration (or start date) recalculates the duration-specific
// minimum workforce immediately — stale requirements are never displayed.
document.getElementById('planDurationMonths')?.addEventListener('change', () => {
    refreshWorkerMinimum();
});
document.getElementById('planStartDate')?.addEventListener('change', () => {
    const durSel = document.getElementById('planDurationMonths');
    if (durSel?.value) refreshWorkerMinimum();
});
document.getElementById('planWorkers')?.addEventListener('input', () => {
    validateWorkersInput();
});
document.getElementById('planWorkers')?.addEventListener('change', () => {
    validateWorkersInput();
});

document.getElementById('calPrevBtn')?.addEventListener('click', () => {
    const v = window._calView;
    const d = new Date(v.y, v.m - 1, 1);
    window._calView = { y: d.getFullYear(), m: d.getMonth() };
    renderCalendar();
});
document.getElementById('calNextBtn')?.addEventListener('click', () => {
    const v = window._calView;
    const d = new Date(v.y, v.m + 1, 1);
    window._calView = { y: d.getFullYear(), m: d.getMonth() };
    renderCalendar();
});

if (document.getElementById('tab-calendar')) _prefillDurationInputs();


// ═══════════════════════════════════════════════════════════════════════════════
// Conversation Management (DB-backed, per-user)
// ═══════════════════════════════════════════════════════════════════════════════

window._currentConvId = null;  // active conversation ID
window._currentBlueprintId = null;
window._uploadAbortController = null;  // for upload cancellation
window._convLoadSeq = 0;  // conversation-load race guard

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ── Recent Chats panel (sidebar "Recent Chats" → tab-chats) ──
// Search is a pure client-side filter over the user's own fetched
// conversations — it never writes to the database. Pin state is stored in the
// conversations table (is_pinned) via the existing ownership-checked PUT API.
let _chatsLastFetched = [];

async function loadConversations() {
    if (window._chatsLoading) return; // prevent duplicate refresh requests
    window._chatsLoading = true;
    const refreshBtn = document.getElementById('refreshChatsBtn');
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Refreshing...';
    }
    try {
        const res = await fetch('/api/conversations');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        _chatsLastFetched = data.conversations || [];
        clearChatsError();
        renderChatsList();
    } catch (e) {
        console.error('loadConversations failed:', e);
        // Keep the previously rendered chats; surface the failure instead.
        showChatsError('Could not refresh chats — showing the last loaded list.');
    } finally {
        window._chatsLoading = false;
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.innerHTML = '<i class="fa-solid fa-rotate"></i> Refresh';
        }
    }
}

function showChatsError(msg) {
    const list = document.getElementById('recentChatsPanel');
    if (!list) return;
    let errEl = document.getElementById('chatsRefreshError');
    if (!errEl) {
        errEl = document.createElement('div');
        errEl.id = 'chatsRefreshError';
        errEl.style.cssText = 'font-size:12px;color:var(--color-red);padding:4px 2px;';
        list.prepend(errEl);
    }
    errEl.textContent = msg;
}

function clearChatsError() {
    document.getElementById('chatsRefreshError')?.remove();
}

function renderChatsList() {
    const list = document.getElementById('recentChatsPanel');
    if (!list) return;
    const convs = _chatsLastFetched;
    if (convs.length === 0) {
        list.innerHTML = '<div style="font-size:12px;color:var(--color-muted);padding:8px 2px;">No recent chats yet. Start a conversation from the Dashboard.</div>';
        return;
    }

    // Case-insensitive title filter applied client-side only
    const q = (document.getElementById('chatsSearchInput')?.value || '').trim().toLowerCase();
    const matches = (c) => !q || String(c.title || '').toLowerCase().includes(q);
    const pinned = convs.filter(c => c.is_pinned && matches(c));
    const recent = convs.filter(c => !c.is_pinned && matches(c));

    if (pinned.length === 0 && recent.length === 0) {
        list.innerHTML = '<div style="font-size:12px;color:var(--color-muted);padding:8px 2px;">No chats found.</div>';
        return;
    }

    const fmtDate = (iso) => { try { return new Date(iso).toLocaleString(); } catch (e) { return ''; } };
    const rowHtml = (c) => {
        const bpBadge = c.has_blueprint
            ? `<span class="rc-blueprint-badge" title="Blueprint: ${escapeHtml(c.blueprint_filename || 'uploaded')}"><i class="fa-solid fa-map"></i></span>`
            : '';
        return `
        <div class="recent-chat-item${c.id === window._currentConvId ? ' active' : ''}" data-conv-id="${c.id}" title="Last activity: ${escapeHtml(fmtDate(c.updated_at))}">
            <i class="fa-solid fa-comment"></i>
            <span class="rc-title">${escapeHtml(c.title || 'Untitled')}</span>
            ${bpBadge}
            <span class="rc-pin${c.is_pinned ? ' pinned' : ''}" data-pin-conv="${c.id}" title="${c.is_pinned ? 'Unpin' : 'Pin'}"><i class="fa-solid fa-thumbtack"></i></span>
            <span class="rc-delete" data-delete-conv="${c.id}" title="Delete"><i class="fa-solid fa-xmark"></i></span>
        </div>`;
    };
    const groupHeader = (label, extraGap) =>
        `<div class="recent-chats-header"${extraGap ? ' style="margin-top:14px;"' : ''}><i class="fa-solid ${label === 'Pinned' ? 'fa-thumbtack' : 'fa-clock-rotate-left'}"></i><span>${label}</span></div>`;

    let html = '';
    if (pinned.length > 0 && recent.length > 0) {
        html += groupHeader('Pinned') + pinned.map(rowHtml).join('');
        html += groupHeader('Recent', true) + recent.map(rowHtml).join('');
    } else {
        html += (pinned.length > 0 ? pinned : recent).map(rowHtml).join('');
    }
    list.innerHTML = html;

    list.querySelectorAll('.recent-chat-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            if (e.target.closest('.rc-delete') || e.target.closest('.rc-pin')) return;
            const cid = parseInt(item.dataset.convId);
            // Open the chat on the Dashboard, then load its messages and
            // restore THAT conversation's own blueprint.
            document.querySelector('.nav-btn[data-tab="dashboard"]')?.click();
            await switchConversation(cid);
        });
    });
    list.querySelectorAll('.rc-pin').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const cid = parseInt(btn.dataset.pinConv);
            const makePinned = !btn.classList.contains('pinned');
            try {
                const res = await fetch(`/api/conversations/${cid}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ is_pinned: makePinned })
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const conv = _chatsLastFetched.find(c => c.id === cid);
                if (conv) conv.is_pinned = makePinned ? 1 : 0;
                renderChatsList(); // instant regroup; DB remains source of truth
            } catch (err) {
                console.error('Pin toggle failed:', err);
                showChatsError('Could not update pin state.');
            }
        });
    });
    list.querySelectorAll('.rc-delete').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const cid = parseInt(btn.dataset.deleteConv);
            if (!confirm('Delete this conversation?')) return;
            await fetch(`/api/conversations/${cid}`, { method: 'DELETE' });
            if (cid === window._currentConvId) window._currentConvId = null;
            loadConversations();
        });
    });
}

document.getElementById('refreshChatsBtn')?.addEventListener('click', loadConversations);

// Live search: refilter the already-fetched list only — no extra requests
document.getElementById('chatsSearchInput')?.addEventListener('input', () => {
    clearChatsError();
    renderChatsList();
});

// Reset the Floor Workspace to its empty state (upload dropzone visible).
// Used when switching chats so a previous conversation's drawing can never
// linger behind a slower-loading blueprint.
function clearWorkspaceView() {
    window._currentBlueprintId = null;
    window.currentImagePath = null;
    window.currentSpatialData = null;
    const dropzone = document.getElementById('uploadDropzone');
    const canvasBox = document.getElementById('canvasContainer');
    const controls = document.getElementById('canvasControls');
    if (dropzone) dropzone.style.display = '';
    if (canvasBox) canvasBox.style.display = 'none';
    if (controls) controls.style.display = 'none';
}

async function switchConversation(convId) {
    // Race guard: if the user clicks another conversation while this load is
    // in flight, the stale response must never render.
    const token = ++window._convLoadSeq;
    window._currentConvId = convId;
    window._titleSet = true;
    clearWorkspaceView();
    // A different conversation must never inherit the previous chat's
    // blueprint worker requirement — it is refetched for THIS conversation.
    resetWorkerMinimumContext();
    // ...nor its Design Studio result or in-flight generation.
    resetDesignStudioState();
    const chatBody = document.getElementById('chatHistory');
    chatBody.innerHTML = '';

    // Temporary loading state until the COMPLETE history has arrived
    const loadingEl = document.createElement('div');
    loadingEl.className = 'chat-message system-message loading-placeholder';
    loadingEl.innerHTML = `
        <div class="msg-icon"><i class="fa-solid fa-spinner fa-spin"></i></div>
        <div class="msg-content"><p>Loading conversation...</p></div>`;
    chatBody.appendChild(loadingEl);

    try {
        const res = await fetch(`/api/conversations/${convId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (token !== window._convLoadSeq) return; // user switched away mid-load

        loadingEl.remove();

        // Render with the SAME message model and renderer used by live chat:
        // user bubbles stay plain text; assistant output goes through the
        // same markdown formatter as the Coordinator synthesis bubble.
        const msgs = data.messages || [];
        const userCount = msgs.filter(m => m.role === 'user').length;
        const assistantCount = msgs.filter(m => m.role === 'assistant').length;
        console.debug(`[BuildSense] Loaded conversation ${convId}: ${msgs.length} messages (${userCount} user, ${assistantCount} assistant)`);

        if (msgs.length === 0) {
            addChatMessage('system', '<p>This conversation has no messages yet.</p>');
        }
        msgs.forEach(m => {
            const content = typeof m.content === 'string' ? m.content : '';
            if (m.role === 'user') {
                addChatMessage('user', `<p>${escapeHtml(content)}</p>`, null, m.id);
            } else {
                const formatted = (typeof window._bsFormatMarkdown === 'function')
                    ? window._bsFormatMarkdown(content)
                    : escapeHtml(content);
                addChatMessage('assistant',
                    `<div class="recommendation-synthesis">${formatted}</div>`, null, m.id);
            }
        });

        // Restore the blueprint associated with this conversation (saved
        // analysis only — never re-runs the analyzer). MUST be awaited so
        // the workspace is in its final state before this function returns;
        // without await the clearWorkspaceView() dropzone flash is visible.
        await restoreBlueprintContext(convId, token);

        chatBody.scrollTop = chatBody.scrollHeight;
    } catch (e) {
        console.error('switchConversation failed:', e);
        if (token === window._convLoadSeq) {
            loadingEl.remove();
            addChatMessage('system', '<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> Failed to load conversation.</p>');
        }
    }
    loadConversations();
}

async function restoreBlueprintContext(convId, token) {
    let bp = null;
    try {
        const listRes = await fetch(`/api/blueprints?conversation_id=${convId}`);
        if (!listRes.ok) return;
        const listData = await listRes.json();
        bp = (listData.blueprints || [])[0];
        if (!bp) {
            // This conversation has no blueprint of its own — clear any
            // drawing left over from a previously opened chat so the
            // workspace never shows another conversation's blueprint.
            if (token !== window._convLoadSeq || window._currentConvId !== convId) return;
            clearWorkspaceView();
            return;
        }

        const bpRes = await fetch(`/api/blueprints/${bp.id}`);
        if (!bpRes.ok) {
            // A blueprint IS linked to this chat but could not be retrieved —
            // never silently fall back to another chat's (or no) drawing.
            console.warn(`[BuildSense] Blueprint ${bp.id} metadata request failed: HTTP ${bpRes.status}`);
            if (token === window._convLoadSeq && window._currentConvId === convId) {
                addChatMessage('system', '<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> This chat\'s saved blueprint could not be loaded. Please try reopening the chat.</p>');
            }
            return;
        }
        const bpFull = await bpRes.json();
        if (token !== window._convLoadSeq) return; // conversation changed meanwhile
        if (window._currentConvId !== convId) return;

        console.debug(`[BuildSense] Restoring blueprint ${bp.id} (${bp.filename}) for conversation ${convId}`);

        let sd = bpFull.analysis && bpFull.analysis.spatial_data;
        if (!sd || typeof sd !== 'object') {
            // The blueprint FILE exists but its saved analysis could not be
            // retrieved. Still show the actual drawing with empty overlays —
            // an analysis gap must not blank the workspace.
            console.debug('[BuildSense] Saved analysis unavailable for blueprint', bp.id, '- restoring image only');
            sd = { rooms: [], corridors: [], exits: [], raw_analysis: 'Saved analysis unavailable for this blueprint.' };
        }
        // Normalize legacy analyses so the canvas renderer has its arrays
        if (!Array.isArray(sd.rooms)) sd.rooms = [];
        if (!Array.isArray(sd.corridors)) sd.corridors = [];
        if (!Array.isArray(sd.exits)) sd.exits = [];

        window._currentBlueprintId = bp.id;
        if (typeof window.handleBlueprintData === 'function') {
            window.handleBlueprintData({
                image_path: bp.file_path,
                file_url: `/uploads/${bp.filename}`,
                spatial_data: sd
            }, { silent: true });
        }
    } catch (e) {
        console.warn('[BuildSense] Blueprint context restore failed:', e);
        // Only surface an error when we KNOW this chat has a blueprint
        // (otherwise an empty workspace is the correct state).
        if (typeof bp !== 'undefined' && bp && token === window._convLoadSeq && window._currentConvId === convId) {
            addChatMessage('system', '<p class="text-danger"><i class="fa-solid fa-circle-exclamation"></i> This chat\'s saved blueprint could not be loaded. Please try reopening the chat.</p>');
        }
    }
}

async function ensureConversation() {
    if (window._currentConvId) return window._currentConvId;
    try {
        const res = await fetch('/api/conversations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: 'New Chat' })
        });
        const data = await res.json();
        window._currentConvId = data.conversation?.id;
        window._titleSet = false;
        loadConversations();
        return window._currentConvId;
    } catch (e) {
        console.error('ensureConversation failed:', e);
        return null;
    }
}

// Deterministic short chat names: strip conversational filler from the user's
// opening message, then use the first few significant words. Generated once
// per conversation (guarded by window._titleSet) and never re-derived after.
const _CHAT_TITLE_FILLERS = [
    'hey buildsense', 'hi buildsense', 'hello buildsense', 'buildsense',
    'hey', 'hi', 'hello', 'please', 'kindly',
    'can you', 'could you', 'can we', 'could we', 'should we',
    'would you', 'will you',
    'tell me', 'show me', 'give me', 'help me',
    'what is the', 'what is', 'whats the', 'whats', 'what are',
    'how much does', 'how much', 'how many', 'how do i', 'how can i', 'how to',
    'do we', 'does this', 'is there', 'are there',
    'i want to', 'i want', 'i need to', 'i need', 'i would like'
];

function deriveChatTitle(rawText) {
    let text = String(rawText || '')
        .toLowerCase()
        .replace(/'/g, '')
        .replace(/[^a-z0-9\s]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
    let stripped = true;
    while (stripped && text) {
        stripped = false;
        for (const phrase of _CHAT_TITLE_FILLERS) {
            if (text === phrase) { text = ''; stripped = true; break; }
            if (text.startsWith(phrase + ' ')) {
                text = text.slice(phrase.length).trim();
                stripped = true;
            }
        }
    }
    const words = text.split(' ').filter(Boolean);
    if (words.length === 0) return '';
    const title = words.slice(0, 6).join(' ').slice(0, 48);
    return title.charAt(0).toUpperCase() + title.slice(1);
}

async function autoTitleConversation(convId, firstMessage) {
    const title = deriveChatTitle(firstMessage);
    if (!title) return; // nothing meaningful -> keep the default 'New Chat'
    try {
        await fetch(`/api/conversations/${convId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title })
        });
        loadConversations();
    } catch (e) { }
}

// ── New Chat button ──
document.getElementById('newChatBtn')?.addEventListener('click', async () => {
    window._convLoadSeq++;  // cancel any in-flight conversation load
    window._currentConvId = null;
    window._titleSet = false;
    // A brand-new conversation must NOT inherit the previous chat's
    // blueprint — clear the Floor Workspace back to its empty state.
    clearWorkspaceView();
    // ...and never inherit its worker minimum either.
    resetWorkerMinimumContext();
    // ...nor its Design Studio result or in-flight generation.
    resetDesignStudioState();
    const chatBody = document.getElementById('chatHistory');
    chatBody.innerHTML = `
        <div class="chat-message system-message">
            <div class="msg-icon"><i class="fa-solid fa-robot"></i></div>
            <div class="msg-content">
                <h3>Welcome to BuildSense</h3>
                <p>Upload an architectural floor plan drawing on the left, then ask me questions about budget matching, fire code compliance, labor timelines, or resource scheduling.</p>
                <div class="quick-presets">
                    <span class="preset-title">Try this query preset:</span>
                    <button class="btn btn-preset-chip" id="presetQueryBtn">
                        "Can we finish Phase 2 within a ₹15 lakh budget while staying compliant with fire safety norms?"
                    </button>
                </div>
            </div>
        </div>`;
    document.getElementById('presetQueryBtn')?.addEventListener('click', () => {
        document.getElementById('chatInput').value = 'Can we finish Phase 2 within a ₹15 lakh budget while staying compliant with fire safety norms?';
        document.getElementById('sendBtn').click();
    });
    await ensureConversation();
    loadConversations();
});

// Load conversations on startup
loadConversations();


// ═══════════════════════════════════════════════════════════════════════════════
// Blueprint Reset
// ═══════════════════════════════════════════════════════════════════════════════

document.getElementById('resetBlueprintBtn')?.addEventListener('click', async () => {
    if (!confirm('Reset the current blueprint? This will clear the current view but keep your conversation.')) return;
    window.currentImagePath = null;
    window.currentSpatialData = null;
    window._currentBlueprintId = null;
    document.getElementById('uploadDropzone').style.display = '';
    document.getElementById('canvasContainer').style.display = 'none';
    document.getElementById('canvasControls').style.display = 'none';
    const canvas = document.getElementById('blueprintCanvas');
    if (canvas) {
        const ctx2 = canvas.getContext('2d');
        ctx2.clearRect(0, 0, canvas.width, canvas.height);
    }
});


// ═══════════════════════════════════════════════════════════════════════════════
// Upload Cancellation / Stop
// ═══════════════════════════════════════════════════════════════════════════════

document.getElementById('stopUploadBtn')?.addEventListener('click', () => {
    if (window._uploadAbortController) {
        window._uploadAbortController.abort();
        window._uploadAbortController = null;
    }
    document.getElementById('uploadProgressBar').style.display = 'none';
    document.getElementById('uploadDropzone').style.display = '';
});


// ═══════════════════════════════════════════════════════════════════════════════
// Chat message persistence
// ═══════════════════════════════════════════════════════════════════════════════
// Persistence is handled in ONE place: the backend persists both the user
// question and the final assistant response when /api/query completes.
// sendQuery() forwards conversation_id / blueprint_id so the backend can do
// this, and stamps the returned message ids onto the rendered bubbles.
// There is intentionally NO client-side message saving and NO fetch patching —
// duplicate savers were causing incomplete/duplicated chat history.


// ═══════════════════════════════════════════════════════════════════════════════
// Chat → Calendar (Natural Language Event Creation)
// ═══════════════════════════════════════════════════════════════════════════════

async function checkAndCreateCalendarEvent(message) {
    // Only trigger on clear scheduling phrases
    const lower = (message || '').toLowerCase();
    const schedulePatterns = [
        /schedule\b.*\b(?:tomorrow|today|next|at|on)\b/i,
        /(?:create|add|set)\s+(?:a\s+)?(?:calendar|event|meeting|review|inspection)/i,
        /\b(?:review|inspection|meeting|visit)\b.*\b(?:tomorrow|today|at|\d{1,2}\s*(?:am|pm))\b/i,
        /\b(?:tomorrow|today)\b.*\b(?:at|@)\s*\d{1,2}\s*(?:am|pm)?\b/i,
    ];
    const matches = schedulePatterns.some(p => p.test(lower));
    if (!matches) return;

    // Extract date
    let eventDate = new Date();
    if (/tomorrow/i.test(lower)) {
        eventDate.setDate(eventDate.getDate() + 1);
    } else if (/next week/i.test(lower)) {
        eventDate.setDate(eventDate.getDate() + 7);
    } else {
        // Try to extract a date like 'august 25' or '25 august' or '25/08'
        const dateMatch = lower.match(/(\d{1,2})\s*(january|february|march|april|may|june|july|august|september|october|november|december)/i);
        if (dateMatch) {
            eventDate = new Date(`${dateMatch[1]} ${dateMatch[2]} ${eventDate.getFullYear()}`);
        } else {
            const dateMatch2 = lower.match(/(january|february|march|april|may|june|july|august|september|october|november|december)\s*(\d{1,2})/i);
            if (dateMatch2) {
                eventDate = new Date(`${dateMatch2[1]} ${dateMatch2[2]} ${eventDate.getFullYear()}`);
            }
        }
    }

    // Extract time — only claim an assumed default when we actually use one
    let startTime = null;
    const timeMatch = lower.match(/(?:at|@)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?/i);
    if (timeMatch) {
        let h = parseInt(timeMatch[1]);
        const m = timeMatch[2] ? parseInt(timeMatch[2]) : 0;
        const ampm = (timeMatch[3] || '').toLowerCase();
        if (ampm === 'pm' && h < 12) h += 12;
        if (ampm === 'am' && h === 12) h = 0;
        startTime = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    }
    const assumedTime = startTime === null;

    // Extract title from scheduling context
    let title = 'Untitled event';
    const titleMatch = message.match(/(?:schedule|create|add)\s+(?:a\s+)?(.+?)(?:\s+(?:tomorrow|today|at|on|at\s+\d))/i);
    if (titleMatch) {
        const extracted = titleMatch[1].trim();
        if (extracted) title = extracted;
    }

    const dateStr = eventDate.toISOString().slice(0, 10);
    try {
        const res = await fetch('/api/user/calendar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: title,
                date: dateStr,
                start_time: startTime,
                description: `Created from chat: "${message.slice(0, 100)}"`,
                category: 'chat-created',
                conversation_id: window._currentConvId || null,
            })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            addChatMessage("system", `<p class="text-danger"><i class='fa-solid fa-circle-exclamation'></i> Could not create calendar event: ${data.error || `server error (${res.status})`}</p>`);
            return;
        }
        const timeNote = assumedTime ? " (assumed 09:00 — no time was specified)" : "";
        addChatMessage("system", `<p><i class='fa-solid fa-calendar-check text-success'></i> Calendar event created: "<strong>${escapeHtml(data.event?.title || title)}</strong>" on ${dateStr}${data.event?.start_time ? ` at ${data.event.start_time}` : ''}${timeNote}.</p>`);
    } catch (e) {
        console.error('Calendar event creation failed:', e);
        addChatMessage("system", `<p class="text-danger"><i class='fa-solid fa-circle-exclamation'></i> Calendar event creation failed: ${e.message}</p>`);
    }
}

