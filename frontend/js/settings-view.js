class SettingsView {
    constructor() {
        this.values = null;
        this.statusTimers = {};

        this.fields = {
            AUDIO_TRACK_MODE: {
                label: 'Audio track mode',
                kind: 'select',
                options: [
                    { value: 'preferred', label: 'Preferred language' },
                    { value: 'all', label: 'All tracks' },
                ],
            },
            SUBTITLE_TRACK_MODE: {
                label: 'Subtitle track mode',
                kind: 'select',
                options: [
                    { value: 'preferred', label: 'Preferred language' },
                    { value: 'all', label: 'All tracks' },
                    { value: 'none', label: 'No subtitles' },
                ],
            },
            PREFERRED_AUDIO_LANGUAGES: {
                label: 'Preferred audio languages',
                kind: 'text',
                hint: 'Comma-separated ISO 639 codes, in order of preference.',
            },
            PREFERRED_SUBTITLE_LANGUAGES: {
                label: 'Preferred subtitle languages',
                kind: 'text',
                hint: 'Comma-separated ISO 639 codes, in order of preference.',
            },
            OUTPUT_FILE_MODE: {
                label: 'Fallback file mode',
                kind: 'text',
                hint: 'Octal mode such as 0644, used when the source mode cannot be copied.',
                machine: true,
            },
            PUID: {
                label: 'File owner UID',
                kind: 'number',
                hint: 'Empty keeps the user that writes the file.',
                min: 0,
                max: 65535,
            },
            PGID: {
                label: 'File owner GID',
                kind: 'number',
                hint: 'Empty keeps the group that writes the file.',
                min: 0,
                max: 65535,
            },
            DISTRIBUTED_ENABLED: {
                label: 'Enable distributed mode',
                kind: 'checkbox',
                hint: 'Only on a trusted LAN, with the library mounted at the same path on every node.',
            },
            DISTRIBUTED_NODE_NAME: {
                label: 'Node name',
                kind: 'text',
            },
            DISTRIBUTED_PUBLIC_URL: {
                label: 'Public URL',
                kind: 'text',
                hint: 'Address other nodes use to reach this service.',
                machine: true,
            },
            DISTRIBUTED_LEADER_URL: {
                label: 'Leader URL',
                kind: 'text',
                hint: 'Leave empty to elect a leader automatically.',
                machine: true,
            },
            DISTRIBUTED_PEERS: {
                label: 'Peers',
                kind: 'text',
                hint: 'Comma-separated peer URLs for discovery without multicast.',
                machine: true,
            },
            DISTRIBUTED_NODE_ID: {
                label: 'Node ID',
                kind: 'text',
                readOnly: true,
            },
            JOB_HISTORY_RETENTION_DAYS: {
                label: 'Keep finished jobs for (days)',
                kind: 'number',
                hint: '0 keeps finished jobs forever.',
                min: 0,
            },
            JOB_HISTORY_MAX_ROWS: {
                label: 'Maximum finished jobs',
                kind: 'number',
                hint: '0 keeps an unlimited number of finished jobs.',
                min: 0,
            },
            LOG_LEVEL: {
                label: 'Log level',
                kind: 'select',
                options: [
                    { value: 'DEBUG', label: 'DEBUG' },
                    { value: 'INFO', label: 'INFO' },
                    { value: 'WARNING', label: 'WARNING' },
                    { value: 'ERROR', label: 'ERROR' },
                ],
            },
        };

        this.sections = {
            tracks: [
                'AUDIO_TRACK_MODE',
                'SUBTITLE_TRACK_MODE',
                'PREFERRED_AUDIO_LANGUAGES',
                'PREFERRED_SUBTITLE_LANGUAGES',
            ],
            output: ['OUTPUT_FILE_MODE', 'PUID', 'PGID'],
            cluster: [
                'DISTRIBUTED_ENABLED',
                'DISTRIBUTED_NODE_NAME',
                'DISTRIBUTED_PUBLIC_URL',
                'DISTRIBUTED_LEADER_URL',
                'DISTRIBUTED_PEERS',
                'DISTRIBUTED_NODE_ID',
            ],
            maintenance: [
                'JOB_HISTORY_RETENTION_DAYS',
                'JOB_HISTORY_MAX_ROWS',
                'LOG_LEVEL',
            ],
        };
    }

    init() {
        document.querySelectorAll('[data-settings-save]').forEach(button => {
            button.addEventListener('click', () => this.save(button.dataset.settingsSave));
        });

        document.getElementById('btn-settings-restart').addEventListener('click', () => this.restart());
    }

    async load() {
        try {
            const data = await api.getSettings();
            this.render(data.settings);
        } catch (error) {
            console.error('Settings could not be loaded:', error);
            if (window.app) {
                window.app.showNotification(`Settings could not be loaded: ${error.message}`, 'danger');
            }
        }
    }

    render(values) {
        this.values = values;

        Object.entries(this.sections).forEach(([section, names]) => {
            const container = document.getElementById(`settings-${section}-fields`);
            container.replaceChildren();
            names.forEach(name => {
                container.appendChild(this.createField(name, values[name]));
            });
        });

        const restartNeeded = Object.values(values).some(setting => setting.restart_required);
        document.getElementById('settings-restart-status').hidden = !restartNeeded;
        document.getElementById('btn-settings-restart').hidden = !restartNeeded;
        document.getElementById('settings-cluster-status').textContent = restartNeeded
            ? 'Restart to apply'
            : '';
    }

    createField(name, setting) {
        const meta = this.fields[name];
        const wrapper = document.createElement('div');
        wrapper.className = 'field';

        if (meta.kind === 'checkbox') {
            const check = document.createElement('label');
            check.className = 'check';
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.id = `setting-${name}`;
            input.checked = Boolean(setting.value);
            input.disabled = setting.source === 'env' || meta.readOnly === true;
            check.appendChild(input);
            check.appendChild(document.createTextNode(meta.label));
            wrapper.appendChild(check);
        } else {
            const label = document.createElement('label');
            label.className = 'field';
            const text = document.createElement('span');
            text.className = 'label';
            text.textContent = meta.label;
            label.appendChild(text);
            label.appendChild(this.createInput(name, meta, setting));
            wrapper.appendChild(label);
        }

        if (meta.hint) {
            wrapper.appendChild(this.hint(meta.hint));
        }

        if (name === 'DISTRIBUTED_NODE_ID') {
            wrapper.appendChild(this.hint(
                setting.source === 'env'
                    ? 'Set by DISTRIBUTED_NODE_ID'
                    : 'Taken from the hostname on first start and stored on this node'
            ));
        } else if (setting.source === 'env') {
            wrapper.appendChild(this.hint(`Set by ${name}`));
        }

        return wrapper;
    }

    createInput(name, meta, setting) {
        let input;

        if (meta.kind === 'select') {
            input = document.createElement('select');
            input.className = 'select';
            meta.options.forEach(option => {
                const element = document.createElement('option');
                element.value = option.value;
                element.textContent = option.label;
                input.appendChild(element);
            });
            input.value = setting.value;
        } else {
            input = document.createElement('input');
            input.type = meta.kind === 'number' ? 'number' : 'text';
            input.className = meta.machine ? 'input machine' : 'input';
            input.value = setting.value === null ? '' : String(setting.value);
            if (meta.min !== undefined) input.min = String(meta.min);
            if (meta.max !== undefined) input.max = String(meta.max);
        }

        input.id = `setting-${name}`;
        input.autocomplete = 'off';
        input.disabled = setting.source === 'env' || meta.readOnly === true;
        return input;
    }

    hint(text) {
        const element = document.createElement('span');
        element.className = 'field__hint';
        element.textContent = text;
        return element;
    }

    readValue(name) {
        const meta = this.fields[name];
        const input = document.getElementById(`setting-${name}`);

        if (meta.kind === 'checkbox') return input.checked;
        if (meta.kind === 'number') {
            if (input.value.trim() === '') {
                return name === 'PUID' || name === 'PGID' ? null : 0;
            }
            return Number(input.value);
        }
        return input.value;
    }

    async save(section) {
        const changes = {};
        this.sections[section].forEach(name => {
            const meta = this.fields[name];
            const input = document.getElementById(`setting-${name}`);
            if (!input || input.disabled || meta.readOnly) return;
            changes[name] = this.readValue(name);
        });

        if (Object.keys(changes).length === 0) return;

        try {
            const data = await api.updateSettings(changes);
            this.render(data.settings);
            const restart = section === 'cluster'
                && Object.values(data.settings).some(setting => setting.restart_required);
            this.setStatus(section, restart ? 'Saved, restart to apply' : 'Saved');
        } catch (error) {
            this.setStatus(section, error.message, true);
            window.app.showNotification(`Settings were not saved: ${error.message}`, 'danger');
        }
    }

    setStatus(section, message, isError = false) {
        const element = document.getElementById(`settings-${section}-status`);
        element.textContent = message;
        element.style.color = isError ? 'var(--fail)' : '';

        clearTimeout(this.statusTimers[section]);
        this.statusTimers[section] = setTimeout(() => {
            element.textContent = '';
            element.style.color = '';
        }, 4000);
    }

    async restart() {
        if (!confirm('Restart the service now? Jobs running on this node must finish first.')) return;

        const button = document.getElementById('btn-settings-restart');
        button.disabled = true;
        button.textContent = 'Restarting…';

        try {
            await api.restartService();
            window.app.showNotification('Restarting the service to apply settings', 'info');
            await this.reloadWhenBack();
        } catch (error) {
            button.disabled = false;
            button.textContent = 'Restart service';
            window.app.showNotification(`Restart failed: ${error.message}`, 'danger');
        }
    }

    async reloadWhenBack() {
        // Give the old process time to exit before polling, or it answers itself.
        await new Promise(resolve => setTimeout(resolve, 3000));
        for (let attempt = 0; attempt < 60; attempt++) {
            try {
                await api.getHealth();
                window.location.reload();
                return;
            } catch (error) {
                await new Promise(resolve => setTimeout(resolve, 2000));
            }
        }
    }
}

const settingsView = new SettingsView();
