const videoElement = document.getElementById('player');
const sourceElement = videoElement.querySelector('source');
let subtitleTrack = videoElement.querySelector('track');
const episodeLine = document.getElementById('playerEpisodeLine');
const prevEpisodeLink = document.getElementById('prevEpisodeLink');
const nextEpisodeLink = document.getElementById('nextEpisodeLink');
let isSoftSwitching = false;

// 1. Inisialisasi Plyr
const player = new Plyr(videoElement, {
    title: window.ANIME_NAME,
    autoplay: true,
    keyboard: { focused: true, global: true },
    captions: { active: true, update: true, language: 'id' }
});

// Pindahkan toast ke dalam kontainer Plyr agar terlihat saat fullscreen
player.on('ready', () => {
    const plyrContainer = document.querySelector('.plyr');
    const toast = document.getElementById('screenshotToast');
    if (plyrContainer && toast) {
        plyrContainer.appendChild(toast);
    }

    // Fitur Resume: Lanjutkan dari detik terakhir jika ada
    if (window.RESUME_TIME > 0) {
        // Gunakan event 'canplay' agar seeking dilakukan saat video sudah siap
        player.once('canplay', () => {
            player.currentTime = window.RESUME_TIME;
        });
    }
});

videoElement.addEventListener('error', (e) => {
    const error = videoElement.error;
    console.error('Video Error Code:', error.code, 'Message:', error.message);
    showToast('Unsupported codec or damaged file. Use VLC.');
    document.querySelector('.video-player-container').style.borderColor = '#ef4444';
});

// Auto Next
player.on('ended', () => {
    if (window.NEXT_EP_PATH && window.NEXT_EP_PATH !== 'None') {
        switchEpisode(window.NEXT_EP_PATH, { autoplay: true });
    }
});

let rpcInterval;
let watchProgressInterval;

function formatTime(seconds) {
    if (isNaN(seconds)) return "00:00";
    if (seconds < 0) return "00:00"; // Tangani nilai negatif atau NaN
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) {
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function updateStatus() {
    const currentTime = player.currentTime;
    const duration = player.duration;
    const timeStr = `${formatTime(currentTime)} / ${formatTime(duration)}`;

    fetch('/update_progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            anime_name: window.ANIME_NAME,
            episode: window.EPISODE_PATH,
            episode_num: window.CURRENT_EP_NUM,
            time_str: timeStr,
            last_seconds: currentTime,
            duration: duration
        })
    });
}

function sendWatchProgress(useKeepalive = false) {
    const currentTime = player.currentTime || 0;
    const duration = player.duration || 0;

    if (!window.ANIME_NAME || !window.EPISODE_PATH || !duration || isNaN(duration)) {
        return;
    }

    const progress = Math.round((currentTime / duration) * 100);
    const payload = {
        anime_name: window.ANIME_NAME,
        episode: window.EPISODE_PATH,
        progress: Math.max(0, Math.min(100, progress)),
        duration: duration,
        current_seconds: currentTime
    };

    fetch('/api/watch-status/progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        keepalive: useKeepalive
    }).catch(() => {});
}

function encodeEpisodePath(path) {
    return path.split('/').map(encodeURIComponent).join('/');
}

function getEpisodeCards() {
    return Array.from(document.querySelectorAll('.player-episode-card[data-episode-path]'));
}

function getEpisodeState(episodePath) {
    const cards = getEpisodeCards();
    const index = cards.findIndex(card => card.dataset.episodePath === episodePath);
    const card = index >= 0 ? cards[index] : null;

    return {
        cards,
        index,
        card,
        previous: index > 0 ? cards[index - 1] : null,
        next: index >= 0 && index < cards.length - 1 ? cards[index + 1] : null
    };
}

function updateEpisodeLink(link, card) {
    if (!link) return;

    if (!card) {
        link.hidden = true;
        link.setAttribute('aria-disabled', 'true');
        link.href = '#';
        link.dataset.episodePath = '';
        return;
    }

    link.hidden = false;
    link.removeAttribute('aria-disabled');
    link.href = card.href;
    link.dataset.episodePath = card.dataset.episodePath;
}

function updatePlayerEpisodeState(episodePath) {
    const state = getEpisodeState(episodePath);

    if (!state.card) {
        return null;
    }

    state.cards.forEach(card => card.classList.remove('active'));
    state.card.classList.add('active');
    state.card.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
        inline: 'center'
    });

    window.EPISODE_PATH = episodePath;
    window.CURRENT_EP_NUM = state.card.dataset.episodeNum || String(state.index + 1);
    window.NEXT_EP_PATH = state.next ? state.next.dataset.episodePath : null;
    window.RESUME_TIME = 0;

    if (episodeLine) {
        episodeLine.textContent = `Episode ${window.CURRENT_EP_NUM} of ${window.TOTAL_EPISODES || state.cards.length}`;
    }

    updateEpisodeLink(prevEpisodeLink, state.previous);
    updateEpisodeLink(nextEpisodeLink, state.next);

    return state;
}

function replaceSubtitleTrack(src) {
    if (!subtitleTrack) return;

    const newTrack = subtitleTrack.cloneNode(false);
    newTrack.src = src;
    subtitleTrack.remove();
    videoElement.appendChild(newTrack);
    subtitleTrack = newTrack;
}

function switchEpisode(episodePath, options = {}) {
    if (!episodePath || episodePath === window.EPISODE_PATH || !sourceElement) {
        return false;
    }

    const state = getEpisodeState(episodePath);
    if (!state.card) {
        return false;
    }

    const shouldPushState = options.pushState !== false;
    const shouldAutoplay = options.autoplay !== undefined ? options.autoplay : !player.paused;
    const encodedEpisode = encodeEpisodePath(episodePath);
    const playerUrl = `/player/${encodeURIComponent(window.ANIME_NAME)}/${encodedEpisode}`;

    sendWatchProgress();
    if (rpcInterval) clearInterval(rpcInterval);
    if (watchProgressInterval) clearInterval(watchProgressInterval);

    isSoftSwitching = true;
    updatePlayerEpisodeState(episodePath);

    player.once('canplay', () => {
        isSoftSwitching = false;
        updateStatus();
        sendWatchProgress();

        if (shouldAutoplay) {
            player.play().catch(() => {});
        }
    });

    sourceElement.src = `/stream/${encodeURIComponent(window.ANIME_NAME)}/${encodedEpisode}`;
    replaceSubtitleTrack(`/subtitle/${encodeURIComponent(window.ANIME_NAME)}/${encodedEpisode}`);
    videoElement.load();

    window.setTimeout(() => {
        isSoftSwitching = false;
    }, 2500);

    if (shouldPushState) {
        history.pushState({ episodePath }, '', playerUrl);
    }

    return true;
}

// Fungsi untuk menghapus status Discord
function clearStatus() {
    fetch('/clear_rpc', { method: 'POST' });
}

player.on('play', () => {
    updateStatus();
    sendWatchProgress();
    if (rpcInterval) clearInterval(rpcInterval);
    if (watchProgressInterval) clearInterval(watchProgressInterval);
    // Update status ke Discord setiap 15 detik agar sinkron (Limit Discord RPC)
    rpcInterval = setInterval(updateStatus, 15000);
    watchProgressInterval = setInterval(sendWatchProgress, 30000);
});

player.on('pause', () => {
    if (isSoftSwitching) {
        return;
    }

    if (rpcInterval) clearInterval(rpcInterval);
    if (watchProgressInterval) clearInterval(watchProgressInterval);
    sendWatchProgress();
    clearStatus();
});

// Hapus status Discord saat pengguna menutup tab atau pindah halaman
window.addEventListener('beforeunload', () => {
    sendWatchProgress(true);
    // Navigator.sendBeacon lebih handal untuk request saat unload halaman
    navigator.sendBeacon('/clear_rpc');
});

history.replaceState({ episodePath: window.EPISODE_PATH }, '', window.location.href);

document.addEventListener('click', (event) => {
    const link = event.target.closest('[data-player-nav]');
    if (!link || link.getAttribute('aria-disabled') === 'true') {
        return;
    }

    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
        return;
    }

    const episodePath = link.dataset.episodePath;
    if (!episodePath) {
        return;
    }

    event.preventDefault();

    if (!switchEpisode(episodePath)) {
        window.location.href = link.href;
    }
});

window.addEventListener('popstate', (event) => {
    const episodePath = event.state && event.state.episodePath;
    if (episodePath) {
        switchEpisode(episodePath, {
            pushState: false,
            autoplay: false
        });
    }
});

function playInVLC() {
    const epPath = encodeEpisodePath(window.EPISODE_PATH);
    const url = `/play/${encodeURIComponent(window.ANIME_NAME)}/${epPath}`;

    fetch(url)
        .then(res => res.json())
        .then(data => {
            if(data.status === 'playing') {
                showToast('Opening video in VLC...');
            } else {
                showToast('Unable to open VLC');
            }
        });
}

function showToast(message) {
    const toast = document.getElementById('screenshotToast');
    const msg = document.getElementById('toastMessage');
    msg.textContent = message;
    toast.classList.add('show');
    
    // Sembunyikan notifikasi setelah 3 detik
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Fitur Screenshot dengan tombol 's'
window.addEventListener('keydown', (e) => {
    // Pastikan tidak sedang mengetik di input search
    if (e.key.toLowerCase() === 's' && e.target.tagName !== 'INPUT') {
        e.preventDefault(); // Mencegah perilaku default browser (misal: search)
        const canvas = document.createElement('canvas');
        canvas.width = videoElement.videoWidth;
        canvas.height = videoElement.videoHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);
        
        const imageData = canvas.toDataURL('image/png');
        
        fetch('/screenshot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: imageData })
        })
        .then(res => res.json())
        .then(data => {
            if(data.status === 'success') {
                showToast('Screenshot saved');
                console.log('Screenshot saved to:', data.path);
            } else {
                showToast('Unable to save screenshot');
            }
        })
        .catch(err => {
            showToast('A system error occurred while saving');
            console.error('Unable to save screenshot:', err);
        });
    }
});

const grid = document.getElementById("episodeGrid");

document.getElementById("scrollLeft").onclick = function() {
    const scrollAmount = grid.clientWidth * 0.8;
    grid.scrollBy({
        left: -scrollAmount,
        behavior: "smooth"
    });
};

document.getElementById("scrollRight").onclick = function() {
    const scrollAmount = grid.clientWidth * 0.8;
    grid.scrollBy({
        left: scrollAmount,
        behavior: "smooth"
    });
};
