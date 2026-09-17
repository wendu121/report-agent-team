/**
 * 统一图标系统（Element Plus SVG 组件名）。
 * 用于 EntityCard / ChatEntry 头像，替代 emoji 文本——矢量、清晰、可按分类区分，
 * 永不使用「插头」之类无意义占位。所有名称均在 @element-plus/icons-vue 中实测存在。
 * 兜底一律用语义中立的图标（Box / Cpu / MagicStick / Bell），保证未知分类也不会退化成怪异图标。
 *
 * 第三方品牌渠道（钉钉/飞书/企微/微信/Telegram/Discord/Slack）不使用通用图标，
 * 而是渲染真实品牌 logo：见 utils/brands.ts 的 brandKey() + components/BrandIcon.vue。
 */
export function pluginIcon(category?: string): string {
  const map: Record<string, string> = {
    综合检索: 'Search',
    新闻: 'Promotion',
    新闻资讯: 'Promotion',
    学术: 'Reading',
    学术文献: 'Notebook',
    百科知识: 'Collection',
    金融: 'Money',
    财经行情: 'Histogram',
    行情: 'DataLine',
    社交: 'ChatDotRound',
    电商: 'ShoppingCart',
    电商数据: 'Goods',
    舆情: 'Bell',
    政策: 'OfficeBuilding',
    企业: 'Postcard',
    企业工商: 'Postcard',
    研报: 'Document',
  };
  return (category && map[category]) || 'Box';
}

export function agentIcon(shape?: string): string {
  if (shape === 'researcher') return 'Search';
  if (shape === 'analyst') return 'TrendCharts';
  if (shape === 'writer') return 'EditPen';
  return 'Cpu';
}

export function skillIcon(category?: string): string {
  const map: Record<string, string> = {
    ESG: 'Sunny',
    财务: 'Money',
    合规: 'Stamp',
    风险: 'Warning',
    行业: 'Compass',
    竞争: 'Trophy',
  };
  return (category && map[category]) || 'MagicStick';
}

export function channelIcon(name = ''): string {
  // 品牌渠道（钉钉/飞书/企微/微信/Telegram/Discord/Slack）由 BrandIcon 渲染真实 logo，
  // 见 utils/brands.ts 的 brandKey()；本函数只负责「非品牌渠道」的兜底图标。
  const lower = name.toLowerCase();
  if (name.includes('本地') || name.includes('测试') || lower.includes('mock')) return 'Monitor';
  if (name.includes('钉钉')) return 'Connection';
  if (name.includes('微信') || name.includes('企微')) return 'ChatDotRound';
  if (name.includes('飞书')) return 'ChatLineRound';
  if (name.includes('Telegram')) return 'ChatLineSquare';
  if (name.includes('Discord')) return 'Headset';
  if (name.includes('Slack')) return 'Comment';
  return 'Bell';
}
