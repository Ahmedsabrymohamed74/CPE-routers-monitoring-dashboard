(() => {
  const conditionList = document.getElementById('signalConditions');
  const addCondition = document.getElementById('addSignalCondition');
  function configureCondition(row) {
    const metric = row.querySelector('[name=condition_metric]');
    const value = row.querySelector('[name=condition_value]');
    const enabled = row.querySelector('[name=condition_enabled]');
    const update = () => {
      const option = metric.selectedOptions[0];
      value.min = option.dataset.min;
      value.max = option.dataset.max;
      value.required = enabled.checked;
      row.querySelector('.condition-unit').textContent = option.dataset.unit;
    };
    metric.addEventListener('change', update);
    enabled.addEventListener('change', update);
    row.querySelector('[data-remove-condition]').addEventListener('click', () => {
      row.remove(); addCondition.disabled = false;
    });
    update();
  }
  conditionList?.querySelectorAll('[data-condition]').forEach(configureCondition);
  if (addCondition) addCondition.disabled = conditionList.children.length >= 32;
  addCondition?.addEventListener('click', () => {
    if (conditionList.children.length >= 32) return;
    const key = window.crypto.randomUUID?.() || ('condition-' + Date.now() + '-' + Math.random().toString(36).slice(2));
    const template = document.getElementById('signalConditionTemplate');
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector('[name=condition_id]').value = key;
    row.querySelector('[name=condition_enabled]').value = key;
    configureCondition(row); conditionList.append(row);
    addCondition.disabled = conditionList.children.length >= 32;
  });
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  document.querySelectorAll('time[datetime]').forEach(node => {
    const value = new Date(node.dateTime);
    if (!Number.isNaN(value.getTime())) node.textContent = value.toLocaleString();
  });
  const dialog = document.getElementById('restartDialog');
  const status = document.getElementById('actionStatus');
  let pending = false;
  const report = (message, error = false) => {
    status.textContent = message;
    status.hidden = false;
    status.classList.toggle('error', error);
  };
  async function post(url, data) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json',
      'X-CSRF-Token': window.CPE_THEME_CONFIG.csrf}, body: JSON.stringify(data)});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || 'The action could not be completed.');
    return payload;
  }
  let targetVendor;
  document.getElementById('restartRouter')?.addEventListener('click', () => {
    const select = document.getElementById('restartVendor');
    targetVendor = select.value;
    document.getElementById('restartTarget').textContent = select.selectedOptions[0].textContent;
    document.getElementById('restartConfirmation').value = '';
    document.getElementById('confirmRestart').disabled = true;
    dialog.showModal();
  });
  document.getElementById('cancelRestart')?.addEventListener('click', () => dialog.close());
  document.getElementById('restartConfirmation')?.addEventListener('input', event => {
    document.getElementById('confirmRestart').disabled = event.target.value !== 'RESTART' || pending;
  });
  document.getElementById('restartForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    if (pending || document.getElementById('restartConfirmation').value !== 'RESTART') return;
    pending = true;
    document.getElementById('confirmRestart').disabled = true;
    document.getElementById('restartRouter').disabled = true;
    dialog.close();
    report('Sending restart request…');
    try {
      const result = await post('/api/actions/restart-router', {vendor: targetVendor, confirmation: 'RESTART'});
      report(result.message, result.status === 'unknown');
    } catch (error) { report(error.message, true); }
    finally { pending = false; document.getElementById('restartRouter').disabled = false; }
  });
  document.getElementById('actionSpeedtest')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    report('Running speed test…');
    try { const result = await post('/api/speedtest/run', {}); report(result.message || 'Speed test completed. View results on the Dashboard.'); }
    catch (error) { report(error.message, true); }
    finally { button.disabled = false; }
  });
})();
