/**
 * Main application
 */
class ConversionApp {
    async init() {
        console.log('Initializing Conversion App...');

        // Initialize theme first
        this.initTheme();

        // Initialize components
        await settingsPanel.init();
        window.settingsPanel = settingsPanel;  // Expose globally
        await fileBrowser.init();
        await jobQueue.init();

        // Connect WebSocket
        wsClient.connect();

        console.log('Conversion App initialized');
    }

    initTheme() {
        const themeToggle = document.getElementById('theme-toggle');
        const html = document.documentElement;
        const icon = themeToggle.querySelector('i');

        // Load saved theme or default to light
        const savedTheme = localStorage.getItem('theme') || 'light';
        html.setAttribute('data-bs-theme', savedTheme);
        this.updateThemeIcon(savedTheme, icon);

        themeToggle.addEventListener('click', () => {
            const currentTheme = html.getAttribute('data-bs-theme');
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            
            html.setAttribute('data-bs-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            this.updateThemeIcon(newTheme, icon);
        });
    }

    updateThemeIcon(theme, icon) {
        if (theme === 'dark') {
            icon.className = 'bi bi-sun-fill';
        } else {
            icon.className = 'bi bi-moon-fill';
        }
    }

    showNotification(message, type = 'info') {
        console.log(`[${type.toUpperCase()}] ${message}`);

        const toastDiv = document.createElement('div');
        const bgClass = type === 'success' ? 'bg-success' : type === 'danger' ? 'bg-danger' : 'bg-primary';

        toastDiv.className = `toast align-items-center text-white ${bgClass} border-0 show position-fixed bottom-0 end-0 m-3`;
        toastDiv.setAttribute('role', 'alert');
        toastDiv.setAttribute('aria-live', 'assertive');
        toastDiv.setAttribute('aria-atomic', 'true');
        toastDiv.style.zIndex = '1050';

        toastDiv.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        `;

        document.body.appendChild(toastDiv);

        setTimeout(() => {
            toastDiv.classList.remove('show');
            setTimeout(() => toastDiv.remove(), 500);
        }, 3000);
    }
}

// Initialize app when DOM is ready
let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new ConversionApp();
    app.init();
    // Expose globally for other components
    window.app = app;
    window.jobQueue = jobQueue;
    window.fileBrowser = fileBrowser;
});
