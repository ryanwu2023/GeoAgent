import React from 'react';

/** Last-resort guard: a render or effect error used to unmount the whole tree and leave
 * only the dark page background. Show what failed and a reload instead. */
export default class AppErrorBoundary extends React.Component<{children: React.ReactNode}, {error: Error | null}> {
  state = {error: null as Error | null};
  static getDerivedStateFromError(error: Error) { return {error}; }
  componentDidCatch(error: Error, info: React.ErrorInfo) { console.error('GeoAgent page crashed', error, info.componentStack); }
  render() {
    const {error} = this.state;
    if (!error) return this.props.children;
    return <div className="app-crash" role="alert">
      <h2>页面出错了</h2>
      <p>页面遇到未处理的错误，已停止渲染。重新加载通常可以恢复；如反复出现，请把下面的错误信息发给管理员。</p>
      <pre>{error.name}: {error.message}</pre>
      <button type="button" className="primary" onClick={() => window.location.reload()}>重新加载</button>
    </div>;
  }
}
