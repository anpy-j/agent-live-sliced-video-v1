/* Shared rule management and read-only S2 matching trials. */
window.renderRules = async function () {
  setCrumb('规则管理');
  const [snapshot, jobData] = await Promise.all([api('/api/rules/pinyin'), api('/api/rules/pinyin/jobs')]);
  if (location.hash !== '#/rules') return;
  app.innerHTML = `<div class="page-head"><div><h1>规则管理</h1><p>V1 流程开关独立控制 · 关闭后仍可试匹配</p></div></div>
    <div class="rules-tabs" role="tablist" aria-label="规则类型">
      <button class="button primary" id="pinyinTab" role="tab" aria-selected="true" aria-controls="pinyinPanel" tabindex="0">拼音限制</button>
      <button class="button ghost" id="repetitionTab" role="tab" aria-selected="false" aria-controls="repetitionPanel" tabindex="-1">重复限制</button>
      <button class="button ghost" id="fillerTab" role="tab" aria-selected="false" aria-controls="fillerPanel" tabindex="-1">口头词清理</button>
    </div>
    <div id="pinyinPanel" role="tabpanel" aria-labelledby="pinyinTab">${rulePipelinePanel("pinyin", "拼音限制", "S2")}
    <section class="panel rules-panel"><h2>连续拼音限制</h2>
    <p>按无声调音节连续匹配。V1 流程开关开启时，保存的启用规则在下一次 S2 执行时生效。V2、V3 保持现有规则行为。</p>
    <form id="pinyinForm" class="rules-form">
      <label class="field">拼音音节<input name="pinyin" required placeholder="例如：xian huo" autocomplete="off"></label>
      <label class="field">备注<input name="note" maxlength="1000" placeholder="可选，例如：现货话术"></label>
      <div><button class="button primary" type="submit">保存</button> <button class="button ghost" type="button" id="pinyinCancel" hidden>取消编辑</button></div>
    </form>
    <p id="pinyinMessage" role="status"></p>
    <div id="pinyinList"></div></section>
    <section class="panel rules-panel"><h2>从已完成任务试匹配</h2>
    <p>选择任务和要测试的拼音规则，读取全部 S2 子句进行匹配。</p>
    <div class="rules-test-controls"><label class="field">已完成任务<select id="pinyinJob" aria-label="已完成任务"><option value="">选择任务</option>${jobData.jobs.map((j, i) => `<option value="${i}">${escapeHtml(j.title)} · ${escapeHtml(j.version)}</option>`).join('')}</select></label>
    <label class="field">规则拼音<select id="pinyinTestRule" aria-label="规则拼音"><option value="">选择规则拼音</option></select></label>
    <button type="button" class="button primary" id="pinyinTest">开始测试</button></div>
    ${jobData.jobs.length ? '' : '<p>暂无带 S2 数据的已完成任务。</p>'}
    <div id="pinyinResults" aria-live="polite"></div></section></div>
    <div id="repetitionPanel" role="tabpanel" aria-labelledby="repetitionTab" hidden>${rulePipelinePanel("repetition", "重复限制", "S2")}
      <section class="panel rules-panel"><h2>全局重复表达试匹配</h2>
      <p>在任务内原 S2 通过的全部子句中全局查重，不限制相隔距离。已剔除的子句不计次数，也不作为首次出现。除有效短语和完整语句外，新增颜色信息点检测：不同说法的“两个色／两种颜色”可归为同组，区分明确的商品和颜色集合。</p>
      <p>使用脚本词表与句式规则，不调用 AI。同一句内的强调或口吃不计为重复；至少出现在两条不同子句中。试匹配包含第一次出现，不修改任务。V1 S2 开关开启后，仅删除后续出现的完整重复子句；短语和颜色信息点重复只记录提示，现有基础粗筛继续执行。</p>
      <form id="repetitionForm" class="rules-form">
        <label class="field">已完成任务<select id="repetitionJob" required><option value="">选择任务</option>${jobData.jobs.map((j, i) => `<option value="${i}">${escapeHtml(j.title)} · ${escapeHtml(j.version)}</option>`).join('')}</select></label>
        <label class="field">检测类型<select id="repetitionMode"><option value="all">信息点 + 有效短语 + 完整语句</option><option value="fact">仅颜色信息点</option><option value="word">仅有效短语</option><option value="sentence">仅完整语句</option></select></label>
        <label class="field">有效短语最少字数<input id="repetitionWordLength" type="number" min="2" max="32" step="1" value="4" required></label>
        <label class="field">语句最少字数<input id="repetitionSentenceLength" type="number" min="2" max="200" step="1" value="4" required></label>
        <label class="field">最少出现子句数<input id="repetitionCount" type="number" min="2" max="10" step="1" value="2" required></label>
        <div><button type="submit" class="button primary" id="repetitionTest">开始测试</button></div>
      </form>
      ${jobData.jobs.length ? '' : '<p>暂无带 S2 数据的已完成任务。</p>'}
      <div id="repetitionResults" aria-live="polite"></div></section>
    </div>
    <div id="fillerPanel" role="tabpanel" aria-labelledby="fillerTab" hidden>${rulePipelinePanel("filler", "口头词清理", "S4")}
      <section class="panel rules-panel"><h2>按拼音清理口头词</h2>
        <p>人工输入需要去掉的口头词拼音，用空格分隔无声调音节。S4 入选文本中任意位置连续拼音命中都会捕捉，支持同音错别字和多次命中。清理范围可选“句首”或“全部”：句首只清理片段开头命中的口头词，句中、句尾命中保留；全部清理句子中的每一个命中。V1 流程开关开启时，保存后在下一次 S4 执行时生效；重新渲染沿用当次 S4 切点。</p>
        <form id="fillerForm" class="rules-form">
          <label class="field">口头词拼音<input name="pinyin" required maxlength="256" placeholder="例如：zhen de / qi shi / wo gao su ni（每次一条）" autocomplete="off"></label>
          <label class="field">清理范围<select name="scope"><option value="all">全部（句中所有命中）</option><option value="leading">句首（仅片段开头）</option></select></label>
          <label class="field">备注<input name="note" maxlength="1000" placeholder="可选"></label>
          <div><button class="button primary" type="submit">保存</button> <button class="button ghost" type="button" id="fillerCancel" hidden>取消编辑</button></div>
        </form><p id="fillerMessage" role="status"></p><div id="fillerList"></div>
      </section>
      <section class="panel rules-panel"><h2>清理测试</h2>
        <p>输入文本预览，或选择已完成的 V1 任务验证 S4 入选片段的实际剪切区间。任务测试只读数据，不修改成片；缺少精确词级边界的命中会跳过并说明原因。</p>
        <form id="fillerTestForm" class="rules-form">
          <label class="field">测试方式<select id="fillerTestMode"><option value="text">输入文本</option><option value="job">V1 任务</option></select></label>
          <label class="field" id="fillerTextField">测试文本<textarea id="fillerText" maxlength="10000" placeholder="真的你秋冬这样穿又洋气又好看"></textarea></label>
          <label class="field" id="fillerJobField" hidden>已完成 V1 任务<select id="fillerJob"><option value="">选择任务</option></select></label>
          <label class="field">测试规则<select id="fillerTestRule"><option value="">全部启用规则</option></select></label>
          <div><button class="button primary" type="submit" id="fillerTest">开始测试</button></div>
        </form><div id="fillerResults" aria-live="polite"></div>
      </section>
    </div>`;
  let fillerReady = false;
  const tabs = [$('#pinyinTab'), $('#repetitionTab'), $('#fillerTab')];
  const activateTab = index => {
    if (index === 2 && !fillerReady) { fillerReady = true; setupFillerRules(); }
    tabs.forEach((tab, i) => {
    tab.setAttribute('aria-selected', String(i === index));
    tab.tabIndex = i === index ? 0 : -1;
    tab.classList.toggle('primary', i === index);
    tab.classList.toggle('ghost', i !== index);
    document.getElementById(tab.getAttribute('aria-controls')).hidden = i !== index;
  });
  };
  tabs.forEach((tab, index) => {
    tab.onclick = () => activateTab(index);
    tab.onkeydown = event => {
      const target = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
        : event.key === 'ArrowRight' ? (index + 1) % tabs.length
        : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : null;
      if (target === null) return;
      event.preventDefault(); activateTab(target); tabs[target].focus();
    };
  });
  setupRepetitionTrial(jobData.jobs);
  setupRulePipeline();
  let rules = snapshot.rules, editing = null, result = null;
  const form = $('#pinyinForm');
  const message = (text) => { $('#pinyinMessage').textContent = text; };
  const reset = () => { editing = null; form.reset(); $('button[type="submit"]', form).textContent = '保存'; $('#pinyinCancel').hidden = true; };
  const clearResult = () => { result = null; $('#pinyinResults').innerHTML = '<p>规则已变化，请重新测试。</p>'; };
  const list = () => {
    const selected = $('#pinyinTestRule').value;
    $('#pinyinTestRule').innerHTML = '<option value="">选择规则拼音</option>' + rules.map(r => `<option value="${r.id}">${escapeHtml(r.pinyin)}${r.note ? ' · ' + escapeHtml(r.note) : ''}${r.enabled ? '' : '（已停用，可试匹配）'}</option>`).join('');
    $('#pinyinTestRule').value = rules.some(r => r.id === selected) ? selected : '';
    $('#pinyinList').innerHTML = `<p>共 ${rules.length} 条</p>${rules.length ? `<ul class="pinyin-rule-list">${rules.map(r => `<li><span class="pinyin-rule-value">${escapeHtml(r.pinyin)}${r.enabled ? '' : ' <small>已停用</small>'}${r.note ? '<br><small>' + escapeHtml(r.note) + '</small>' : ''}</span><div><button class="button ghost small" data-edit-rule="${r.id}">编辑</button> <button class="button ghost small" data-toggle-rule="${r.id}">${r.enabled ? '停用' : '启用'}</button> <button class="button ghost small" data-delete-rule="${r.id}">删除</button></div></li>`).join('')}</ul>` : '<p>暂无拼音规则。</p>'}`;
  };
  $('#pinyinCancel').onclick = reset;
  form.onsubmit = async event => {
    event.preventDefault();
    const button = $('button[type="submit"]', form); button.disabled = true;
    try {
      const fields = new FormData(form);
      const saved = await api(`/api/rules/pinyin${editing ? '/' + editing : ''}`, {method: editing ? 'PUT' : 'POST', body: JSON.stringify({pinyin: fields.get('pinyin'), note: fields.get('note'), enabled: editing ? rules.find(r => r.id === editing).enabled : true})});
      rules = saved.rules; reset(); list(); clearResult(); message('规则已保存。');
    } catch (error) { message(error.message); } finally { button.disabled = false; }
  };
  $('#pinyinList').onclick = async event => {
    const button = event.target.closest('button'); if (!button) return;
    const id = button.dataset.editRule || button.dataset.toggleRule || button.dataset.deleteRule;
    const rule = rules.find(r => r.id === id); if (!rule) return;
    if (button.dataset.editRule) {
      editing = id;
      form.elements.pinyin.value = rule.pinyin;
      form.elements.note.value = rule.note || '';
      $('button[type="submit"]', form).textContent = '保存修改'; $('#pinyinCancel').hidden = false;
      form.elements.pinyin.focus(); return;
    }
    button.disabled = true;
    try {
      const saved = await api('/api/rules/pinyin/' + id, button.dataset.deleteRule ? {method: 'DELETE'} : {method: 'PUT', body: JSON.stringify({...rule, enabled: !rule.enabled})});
      rules = saved.rules; if (editing === id) reset(); list(); clearResult(); message('规则已更新。');
    } catch (error) { message(error.message); button.disabled = false; }
  };
  const showResults = (hitsOnly = true) => {
    const rows = result.rows.filter(r => !hitsOnly || r.hits.length);
    $('#pinyinResults').innerHTML = `<p>共 ${result.total} 条子句，命中 ${result.matched} 条；其中原 S2 通过、现在会剔除 ${result.newly_rejected} 条。</p>
      <label class="rules-check"><input id="pinyinHitsOnly" type="checkbox" ${hitsOnly ? 'checked' : ''}> 只看命中句</label>
      <div class="rules-table-wrap"><table class="rules-table"><thead><tr><th>时间 / ID</th><th>原文 / 拼音</th><th>原 S2 结果</th><th>当前规则命中</th></tr></thead><tbody>${rows.map(r => `<tr><td>${Number(r.start || 0).toFixed(2)}–${Number(r.end || 0).toFixed(2)}s<br>${escapeHtml(r.id)}</td><td>${escapeHtml(r.text)}<br><small>${escapeHtml(r.pinyin)}</small></td><td>${r.s2_usable ? '通过' : '已剔除'}<br><small>${escapeHtml(reasonLabels[r.s2_reason] || r.s2_reason)}</small></td><td>${r.hits.map(h => `${escapeHtml(h.pinyin)}：${escapeHtml(h.text)}`).join('<br>') || '未命中'}</td></tr>`).join('')}</tbody></table></div>${rows.length ? '' : '<p>没有命中句。</p>'}`;
    $('#pinyinHitsOnly').onchange = event => showResults(event.target.checked);
  };
  $('#pinyinTest').onclick = async () => {
    const value = $('#pinyinJob').value;
    if (value === '') { $('#pinyinResults').textContent = '请先选择已完成任务。'; return; }
    const ruleId = $('#pinyinTestRule').value;
    if (!ruleId) { $('#pinyinResults').textContent = '请选择要匹配的规则拼音。'; return; }
    const job = jobData.jobs[Number(value)], button = $('#pinyinTest'); button.disabled = true;
    $('#pinyinResults').textContent = '正在匹配任务 S2 数据…';
    try { result = await api('/api/rules/pinyin/test', {method: 'POST', body: JSON.stringify({job_id: job.id, kind: job.kind, rule_id: ruleId})}); showResults(); }
    catch (error) { $('#pinyinResults').textContent = error.message; } finally { button.disabled = false; }
  };
  $('#pinyinJob').onchange = () => { result = null; $('#pinyinResults').innerHTML = ''; };
  $('#pinyinTestRule').onchange = () => { result = null; $('#pinyinResults').innerHTML = ''; };
  list();
};

function rulePipelinePanel(name, title, stage) {
  return `<section class="panel rules-panel"><h2>${title}流程开关</h2>
    <p>接入版本：V1 · 接入阶段：${stage}。开启后在下一次执行 ${stage} 时加入本模块规则；关闭后仍可试匹配。S1–S5 阶段顺序不变，已生成的任务结果不会自动重算。</p>
    <button type="button" class="button ghost" role="switch" aria-checked="false" aria-label="${title}加入 V1 ${stage}" data-rule-pipeline="${name}" disabled>读取开关…</button>
    ${name === 'repetition' ? '<button type="button" class="button ghost" id="saveRepetitionPipeline" disabled>保存当前检测条件到 V1 S2</button><p>开启时使用已保存的检测条件。试匹配时调整条件不会自动修改流程配置。</p><p id="repetitionPipelineOptions"></p>' : ''}
    <p role="status" id="${name}PipelineMessage"></p></section>`;
}

async function setupRulePipeline() {
  let settings, saving = false;
  const switches = [...document.querySelectorAll('[data-rule-pipeline]')];
  const optionsButton = $('#saveRepetitionPipeline');
  const refresh = () => {
    switches.forEach(button => {
      const feature = settings.features[button.dataset.rulePipeline];
      button.setAttribute('aria-checked', String(feature.enabled));
      button.classList.toggle('primary', feature.enabled); button.classList.toggle('ghost', !feature.enabled);
      button.textContent = feature.enabled ? `已开启 · 加入 V1 ${feature.stage}` : '已关闭 · 仅试匹配';
      button.disabled = saving;
    });
    optionsButton.disabled = saving;
    const o = settings.features.repetition.options;
    const modes = {all:'信息点 + 有效短语 + 完整语句', fact:'仅颜色信息点', word:'仅有效短语', sentence:'仅完整语句'};
    $('#repetitionPipelineOptions').textContent = `已保存：${modes[o.mode]} · 短语至少 ${o.min_word_length} 字 · 语句至少 ${o.min_sentence_length} 字 · 至少 ${o.min_count} 条子句`;
  };
  const save = async (name, payload) => {
    if (!settings || saving) return;
    saving = true; refresh();
    const message = $(`#${name}PipelineMessage`);
    try {
      settings = await api('/api/rules/pipeline/' + name, {method:'PUT', body:JSON.stringify(payload)});
      if (!message.isConnected) return;
      message.textContent = `已保存，下次执行 V1 ${settings.features[name].stage} 生效；试匹配始终可用。`;
    } catch (error) { message.textContent = error.message; }
    finally { saving = false; if (message.isConnected) refresh(); }
  };
  switches.forEach(button => button.onclick = () => {
    const name = button.dataset.rulePipeline;
    save(name, {enabled:!settings.features[name].enabled});
  });
  optionsButton.onclick = () => save('repetition', {enabled:settings.features.repetition.enabled,
    options:{mode:$('#repetitionMode').value, min_word_length:Number($('#repetitionWordLength').value),
      min_sentence_length:Number($('#repetitionSentenceLength').value), min_count:Number($('#repetitionCount').value)}});
  try {
    settings = await api('/api/rules/pipeline');
    if (!switches[0].isConnected) return;
    if (!settings.features) throw new Error('流程开关读取失败，请刷新重试。');
    const o = settings.features.repetition.options;
    $('#repetitionMode').value = o.mode; $('#repetitionWordLength').value = o.min_word_length;
    $('#repetitionSentenceLength').value = o.min_sentence_length; $('#repetitionCount').value = o.min_count;
    refresh();
  } catch (error) { switches.forEach(button => { $(`#${button.dataset.rulePipeline}PipelineMessage`).textContent = error.message; }); }
}

async function setupFillerRules() {
  const form = $('#fillerForm'), output = $('#fillerResults'), message = $('#fillerMessage');
  const scopeLabels = {all: '全部', leading: '句首'};
  let rules = [], jobs = [], editing = null, generation = 0;
  const invalidate = () => { generation++; output.textContent = '规则或测试条件已变化，请重新测试。'; };
  const reset = () => { editing = null; form.reset(); $('button[type="submit"]', form).textContent = '保存'; $('#fillerCancel').hidden = true; };
  const list = () => {
    const selected = $('#fillerTestRule').value;
    $('#fillerTestRule').innerHTML = '<option value="">全部启用规则</option>' + rules.map(r => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.pinyin)} · ${escapeHtml(scopeLabels[r.scope] || '全部')}${r.enabled ? '' : '（已停用，可单独测试）'}</option>`).join('');
    $('#fillerTestRule').value = rules.some(r => r.id === selected) ? selected : '';
    $('#fillerList').innerHTML = `<p>共 ${rules.length} 条</p>` + (rules.length ? `<ul class="pinyin-rule-list">${rules.map(r => `<li><span class="pinyin-rule-value">${escapeHtml(r.pinyin)} <small>${escapeHtml(scopeLabels[r.scope] || '全部')}</small>${r.enabled ? '' : ' <small>已停用</small>'}${r.note ? '<br><small>' + escapeHtml(r.note) + '</small>' : ''}</span><div><button class="button ghost small" data-edit="${escapeHtml(r.id)}">编辑</button> <button class="button ghost small" data-toggle="${escapeHtml(r.id)}">${r.enabled ? '停用' : '启用'}</button> <button class="button ghost small" data-delete="${escapeHtml(r.id)}">删除</button></div></li>`).join('')}</ul>` : '<p>暂无口头词规则，请人工添加。</p>');
  };
  form.onsubmit = async event => {
    event.preventDefault(); const button = $('button[type="submit"]', form); button.disabled = true;
    try {
      const fields = new FormData(form);
      const saved = await api('/api/rules/filler' + (editing ? '/' + editing : ''), {method: editing ? 'PUT' : 'POST', body: JSON.stringify({pinyin: fields.get('pinyin'), scope: fields.get('scope'), note: fields.get('note'), enabled: editing ? rules.find(r => r.id === editing).enabled : true})});
      rules = saved.rules; reset(); list(); invalidate(); message.textContent = '已保存，下次 V1 S4 执行时生效。';
    } catch (error) { message.textContent = error.message; } finally { button.disabled = false; }
  };
  $('#fillerCancel').onclick = reset;
  $('#fillerList').onclick = async event => {
    const button = event.target.closest('button'); if (!button) return;
    const id = button.dataset.edit || button.dataset.toggle || button.dataset.delete;
    const rule = rules.find(r => r.id === id); if (!rule) return;
    if (button.dataset.edit) {
      editing = id; form.elements.pinyin.value = rule.pinyin; form.elements.note.value = rule.note || '';
      form.elements.scope.value = rule.scope || 'all';
      $('button[type="submit"]', form).textContent = '保存修改'; $('#fillerCancel').hidden = false; return;
    }
    button.disabled = true;
    try {
      const saved = await api('/api/rules/filler/' + id, button.dataset.delete ? {method:'DELETE'} : {method:'PUT', body:JSON.stringify({...rule, enabled: !rule.enabled})});
      rules = saved.rules; if (editing === id) reset(); list(); invalidate(); message.textContent = '规则已更新。';
    } catch (error) { message.textContent = error.message; button.disabled = false; }
  };
  $('#fillerTestForm').oninput = invalidate;
  $('#fillerTestMode').onchange = () => {
    $('#fillerTextField').hidden = $('#fillerTestMode').value !== 'text';
    $('#fillerJobField').hidden = $('#fillerTestMode').value !== 'job'; invalidate();
  };
  $('#fillerTestForm').onsubmit = async event => {
    event.preventDefault(); const button = $('#fillerTest'), version = ++generation;
    const payload = {rule_id: $('#fillerTestRule').value || null};
    if ($('#fillerTestMode').value === 'job') {
      const job = jobs[Number($('#fillerJob').value)];
      if ($('#fillerJob').value === '' || !job) { output.textContent = '请选择已完成的 V1 任务。'; return; }
      payload.job_id = job.id;
    } else { payload.text = $('#fillerText').value; }
    button.disabled = true; output.textContent = '正在测试…';
    try {
      const result = await api('/api/rules/filler/test', {method:'POST', body:JSON.stringify(payload)});
      if (version !== generation || !output.isConnected) return;
      output.innerHTML = `<p>共 ${result.total} 条，命中 ${result.matched} 条，可精确剪切 ${result.cleaned} 条。</p><div class="rules-table-wrap"><table class="rules-table"><thead><tr><th>原文 / 区间</th><th>清理结果 / 区间</th><th>命中 / 处理说明</th></tr></thead><tbody>${result.rows.map(r => `<tr><td>${escapeHtml(r.text)}<br><small>${escapeHtml(r.pinyin || '')}</small>${r.start == null ? '' : `<br><small>${Number(r.start).toFixed(3)}–${Number(r.end).toFixed(3)}s · ID ${escapeHtml(r.id)}</small>`}</td><td>${escapeHtml(r.cleaned_text)}${r.retained_parts ? `<br><small>保留区间：${r.retained_parts.map(p => `${Number(p.start).toFixed(3)}–${Number(p.end).toFixed(3)}s`).join(' / ')} · 减少 ${Number(r.removed_seconds).toFixed(3)}s</small>` : ''}${r.status === 'skipped' ? `<br><small>建议文本：${escapeHtml(r.preview_text)}</small>` : ''}</td><td>${r.hits.map(h => `${escapeHtml(h.pinyin)}：${escapeHtml(h.text)}（字符 ${h.start + 1}–${h.end}）`).join('<br>') || '未命中'}<br><small>${escapeHtml(r.reason)}</small></td></tr>`).join('')}</tbody></table></div>`;
    } catch (error) { if (version === generation) output.textContent = error.message; }
    finally { button.disabled = false; }
  };
  try {
    const [snapshot, data] = await Promise.all([api('/api/rules/filler'), api('/api/rules/filler/jobs')]);
    if (!form.isConnected) return;
    rules = snapshot.rules; jobs = data.jobs;
    $('#fillerJob').innerHTML = '<option value="">选择任务</option>' + jobs.map((j, i) => `<option value="${i}">${escapeHtml(j.title)}</option>`).join('');
    list();
  } catch (error) { message.textContent = error.message; }
}

function setupRepetitionTrial(jobs) {
  let result = null, requestVersion = 0;
  const output = $('#repetitionResults'), form = $('#repetitionForm');
  const typeLabel = type => ({word:'有效短语', sentence:'完整语句', fact:'重复信息点'}[type] || type);
  const highlighted = member => escapeHtml(member.full_text.slice(0, member.char_start))
    + `<mark>${escapeHtml(member.full_text.slice(member.char_start, member.char_end))}</mark>`
    + escapeHtml(member.full_text.slice(member.char_end))
    + (member.fact ? `<br><small>本句识别颜色：${escapeHtml(member.fact.observed_colors.join(' / ') || '未明确识别')} · ${member.fact.complete_colors.length ? '集合完整' : '集合未完整识别'}</small>` : '');
  const showResults = (hitsOnly = true) => {
    const rows = result.rows.filter(row => !hitsOnly || row.hits.length);
    output.innerHTML = `<p>共 ${result.total} 条子句，原 S2 通过 ${result.eligible} 条参与检测，已剔除 ${result.excluded} 条不参与。发现 ${result.group_count} 个重复组，涉及 ${result.matched} 条子句（信息点 ${result.fact_matched} 条，有效短语 ${result.word_matched} 条，完整语句 ${result.sentence_matched} 条，可重叠）。首次出现也纳入展示，未执行剔除。</p>
      <div class="repetition-groups">${result.groups.map(group => `<details class="repetition-group" data-repeat-group="${escapeHtml(group.id)}" open><summary>${typeLabel(group.type)}：${escapeHtml(group.phrase)} · ${group.count} 条子句</summary><p>${escapeHtml(group.reason)}${Object.keys(group.context).length ? ' · 对象：' + escapeHtml(Object.values(group.context).join(' / ')) : ''}</p><div class="rules-table-wrap"><table class="rules-table"><thead><tr><th>时间 / ID</th><th>原文与命中位置</th><th>出现顺序</th></tr></thead><tbody>${group.occurrences.map((member, index) => `<tr><td>${Number(member.start || 0).toFixed(2)}–${Number(member.end || 0).toFixed(2)}s<br>${escapeHtml(member.id)}</td><td>${highlighted(member)}</td><td>${index === 0 ? '首次出现' : '后续出现'}<br><small>字符 ${member.char_start + 1}–${member.char_end}</small></td></tr>`).join('')}</tbody></table></div></details>`).join('')}</div>
      <h3>子句明细</h3>
      <label class="rules-check"><input id="repetitionHitsOnly" type="checkbox" ${hitsOnly ? 'checked' : ''}> 只看命中句</label>
      <div class="rules-table-wrap"><table class="rules-table" id="repetitionRows"><thead><tr><th>时间 / ID</th><th>原文</th><th>原 S2 结果</th><th>重复表达 / 首次出现</th></tr></thead><tbody>${rows.map(row => `<tr><td>${Number(row.start || 0).toFixed(2)}–${Number(row.end || 0).toFixed(2)}s<br>${escapeHtml(row.id)}</td><td>${escapeHtml(row.text)}</td><td>${row.s2_usable ? '通过' : '已剔除'}<br><small>${escapeHtml(reasonLabels[row.s2_reason] || row.s2_reason)}</small></td><td>${row.hits.map(hit => `<div><strong>${typeLabel(hit.type)}</strong>：${escapeHtml(hit.phrase)} · ${hit.count} 条子句<br>${escapeHtml(hit.text)}<br><small>${hit.is_first ? '本句为首次出现' : `首次：${Number(hit.reference.start || 0).toFixed(2)}–${Number(hit.reference.end || 0).toFixed(2)}s · ID ${escapeHtml(hit.reference.id)} · ${escapeHtml(hit.reference.text)}`}</small></div>`).join('') || (row.s2_usable ? '未命中' : '已剔除，不参与重复检测')}</td></tr>`).join('')}</tbody></table></div>${rows.length ? '' : '<p>没有命中句。</p>'}`;
    $('#repetitionHitsOnly').onchange = event => showResults(event.target.checked);
  };
  form.oninput = () => {
    requestVersion++; result = null;
    output.textContent = '检测条件已变化，请重新测试。';
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const value = $('#repetitionJob').value;
    if (value === '') { output.textContent = '请先选择已完成任务。'; return; }
    const job = jobs[Number(value)], button = $('#repetitionTest');
    const version = ++requestVersion;
    const payload = {job_id: job.id, kind: job.kind, mode: $('#repetitionMode').value,
      min_word_length: Number($('#repetitionWordLength').value),
      min_sentence_length: Number($('#repetitionSentenceLength').value), min_count: Number($('#repetitionCount').value)};
    button.disabled = true; output.textContent = '正在检测任务 S2 数据中的重复内容…';
    try {
      const response = await api('/api/rules/repetition/test', {method: 'POST', body: JSON.stringify(payload)});
      if (version !== requestVersion || !output.isConnected) return;
      result = response; showResults();
    } catch (error) { if (version === requestVersion) output.textContent = error.message; }
    finally { button.disabled = false; }
  };
}
