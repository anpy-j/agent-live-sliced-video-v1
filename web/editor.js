/* Independent editor entry; expanded in the editor implementation step. */
window.LiveCutEditor = {
  async mount(root) {
    root.innerHTML = '<div class="hero"><div><h1>人工剪辑</h1><p>独立编辑工作区</p></div></div>';
    document.querySelector('#pageCrumb').textContent = '人工剪辑';
  },
  async settings(root) {
    root.innerHTML = '<div class="hero"><div><h1>剪辑与导出</h1><p>编辑器独立配置</p></div></div>';
  }
};
