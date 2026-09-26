import DefaultTheme from 'vitepress/theme'
import { enhanceAppWithTabs } from 'vitepress-plugin-tabs/client'
import './style.css'

/**
 * 站点主题: 在默认主题基础上注册 vitepress-plugin-tabs 的 Tabs 组件。
 * 对应 markdown 侧的 tabsMarkdownPlugin (见 config.mts)。
 */
export default {
  extends: DefaultTheme,
  enhanceApp({ app }: { app: any }) {
    enhanceAppWithTabs(app)
  }
}
