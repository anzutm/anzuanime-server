const videoElement = document.getElementById('player');

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
    showToast('Codec tidak didukung atau file rusak. Gunakan VLC.');
    document.querySelector('.video-player-container').style.borderColor = '#ef4444';
});

// Auto Next
player.on('ended', () => {
    if (window.NEXT_EP_PATH && window.NEXT_EP_PATH !== 'None') {
        const epPath = window.NEXT_EP_PATH.split('/').map(encodeURIComponent).join('/');
        window.location.href = `/player/${encodeURIComponent(window.ANIME_NAME)}/${epPath}`;
    }
});

let rpcInterval;

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
            last_seconds: currentTime
        })
    });
}

// Fungsi untuk menghapus status Discord
function clearStatus() {
    fetch('/clear_rpc', { method: 'POST' });
}

player.on('play', () => {
    updateStatus();
    if (rpcInterval) clearInterval(rpcInterval);
    // Update status ke Discord setiap 15 detik agar sinkron (Limit Discord RPC)
    rpcInterval = setInterval(updateStatus, 15000);
});

player.on('pause', () => {
    if (rpcInterval) clearInterval(rpcInterval);
    clearStatus();
});

// Hapus status Discord saat pengguna menutup tab atau pindah halaman
window.addEventListener('beforeunload', () => {
    // Navigator.sendBeacon lebih handal untuk request saat unload halaman
    navigator.sendBeacon('/clear_rpc');
});

function playInVLC() {
    const epPath = window.EPISODE_PATH.split('/').map(encodeURIComponent).join('/');
    const url = `/play/${encodeURIComponent(window.ANIME_NAME)}/${epPath}`;

    fetch(url)
        .then(res => res.json())
        .then(data => {
            if(data.status === 'playing') {
                showToast('Membuka video di VLC...');
            } else {
                showToast('Gagal membuka VLC');
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
                showToast('Screenshot berhasil disimpan!');
                console.log('Screenshot disimpan ke:', data.path);
            } else {
                showToast('Gagal menyimpan screenshot');
            }
        })
        .catch(err => {
            showToast('Terjadi kesalahan sistem saat menyimpan');
            console.error('Gagal menyimpan screenshot:', err);
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