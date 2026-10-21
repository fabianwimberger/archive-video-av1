class ConversionApp {
    constructor() {
        this.views = ['convert', 'queue', 'history', 'presets', 'cluster'];
        this.currentView = null;
        this.toastContainer = null;
    }

    async init() {
        await presetManager.init();
        await settingsPanel.init();
        window.settingsPanel = settingsPanel;
        await fileBrowser.init();
        await jobQueue.init();
        await historyView.init();
        window.historyView = historyView;

        this.setupViewSwitching();
        this.setupDialogs();

        wsClient.connect();
        this.handleHash();
    }

    setupViewSwitching() {
        document.querySelectorAll('[data-view-link]').forEach(link => {
            link.addEventListener('click', (event) => {
                event.preventDefault();
                this.switchView(link.dataset.viewLink);
            });
        });
        window.addEventListener('hashchange', () => this.handleHash());
    }

    setupDialogs() {
        document.querySelectorAll('dialog').forEach(dialog => {
            dialog.addEventListener('click', (event) => {
                // A click on the backdrop lands on the dialog element itself.
                if (event.target === dialog) dialog.close();
            });
        });

        document.querySelectorAll('[data-dialog-close]').forEach(button => {
            button.addEventListener('click', () => {
                const dialog = button.closest('dialog');
                if (dialog) dialog.close();
            });
        });
    }

    handleHash() {
        const requested = window.location.hash.replace(/^#\/?/, '') || 'convert';
        const view = this.views.includes(requested) ? requested : 'convert';
        if (view === this.currentView) return;
        this.switchView(view);
    }

    switchView(view) {
        if (!this.views.includes(view)) view = 'convert';

        const changed = this.currentView !== view;
        this.currentView = view;

        document.querySelectorAll('[data-view-panel]').forEach(panel => {
            panel.hidden = panel.dataset.viewPanel !== view;
        });

        document.querySelectorAll('[data-view-link]').forEach(link => {
            if (link.dataset.viewLink === view) {
                link.setAttribute('aria-current', 'page');
            } else {
                link.removeAttribute('aria-current');
            }
        });

        const hash = `#/${view}`;
        if (window.location.hash !== hash) {
            window.location.hash = hash;
        }

        if (changed) this.refreshView(view);
    }

    refreshView(view) {
        if (view === 'convert' || view === 'queue') jobQueue.loadJobs();
        if (view === 'history') historyView.loadJobs();
        if (view === 'presets') presetManager.open();
        if (view === 'cluster') jobQueue.loadClusterStatus();
    }

    showNotification(message, type = 'info') {
        const kinds = { success: 'ok', danger: 'fail', warning: 'warn', info: 'info' };
        if (!this.toastContainer) {
            this.toastContainer = document.createElement('div');
            this.toastContainer.className = 'toasts';
            this.toastContainer.setAttribute('aria-live', 'polite');
            document.body.appendChild(this.toastContainer);
        }

        const toast = document.createElement('div');
        toast.className = `toast toast--${kinds[type] || 'info'}`;
        toast.setAttribute('role', 'status');
        toast.innerHTML = `
            <span class="toast__text">${utils.escapeHtml(message)}</span>
            <button type="button" class="btn btn--text btn--small" aria-label="Dismiss">Dismiss</button>
        `;

        const dismiss = () => toast.remove();
        toast.querySelector('button').addEventListener('click', dismiss);
        this.toastContainer.appendChild(toast);
        setTimeout(dismiss, 5000);
    }
}

let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new ConversionApp();
    app.init();
    window.app = app;
    window.jobQueue = jobQueue;
    window.fileBrowser = fileBrowser;
    window.presetManager = presetManager;
    window.historyView = historyView;
});
