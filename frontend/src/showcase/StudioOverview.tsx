import {Icon} from '../components/Icon'

/** An original, labelled layout diagram; it never impersonates a real task. */
export function StudioOverview() {
  return <div className="showcase-studio-diagram" aria-label="工作台结构示意，不包含真实任务或生成图片">
    <div className="showcase-diagram-top"><span><Icon name="layers"/>Quick-A 工作台</span><span>结构示意</span></div>
    <div className="showcase-diagram-body"><div className="showcase-diagram-slots"><strong>图位</strong><span className="showcase-diagram-selected">主图</span><span>卖点图</span><span>场景图</span></div>
      <div className="showcase-diagram-inputs"><strong>素材与要求</strong><div><Icon name="image"/><span>真实商品</span></div><div><Icon name="image"/><span>同款参考</span></div><span>整体要求 · 逐图要求</span></div>
      <div className="showcase-diagram-output"><strong>候选与版本</strong><div className="showcase-diagram-canvas"><Icon name="image"/><span>成果展示区域</span><small>案例图片待授权</small></div><span>版本历史 → 人工审核 → 有效交付</span></div>
    </div>
  </div>
}
