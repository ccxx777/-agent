# 前端工作区

这是项目的 React + Vite 前端。当前前端包含两条入口：

- **通用 RAG 对话**：保留原有的知识库问答能力。
- **合同风险审查**：面向中国大陆通用劳动合同的上传、解析状态查看、事实确认和风险报告。

视觉基线来自 `stitch_ai_document_chat/`：深色 Zinc 风格、玻璃面板、蓝色主按钮和窄侧栏。该目录是本地设计参考，不参与运行时加载。

## 合同审查页面

合同审查通过 `src/components/ContractReviewPage.tsx` 组织四个阶段：

1. 上传 PDF、DOC 或 DOCX；
2. 轮询后端的解析、脱敏和事实提取状态；
3. 以可折叠事实卡片展示合同原值、当前采用值、证据定位和待确认问题；用户只需要选择确认原文、修改当前采用值、标记不适用或暂不确认。用户本次真正修改的值作为新的用户来源提交；已保存且未修改的纠正/补充动作会保留原 provenance，不会被前端重分类；
4. 在确认完成后调用合同审查 Workflow，展示风险等级、合同证据、法律来源和免责声明。

`src/api/contractReviews.ts` 只负责 HTTP 数据契约。前端不会上传或展示未脱敏原始图片，也不会把用户修改覆盖到原始提取值。

## 本地开发

```bash
npm install
npm run dev
```

开发服务器会将 `/api` 请求代理到 `http://127.0.0.1:8000`。需要先启动后端并登录；登录后从侧栏进入“合同风险审查”。

## 校验

```bash
npm run lint
npm run build
```

## 目录说明

```text
src/
├── api/contractReviews.ts          # 合同审查 API 和 TypeScript 契约
├── components/ContractReviewPage.tsx # 上传、状态、事实确认、报告页面
├── components/Sidebar.tsx          # Stitch 风格侧栏与入口导航
├── contexts/AuthContext.tsx        # 登录会话
├── hooks/useChatStream.ts          # 原有 RAG 对话流
└── index.css                       # 设计令牌、响应式布局和组件样式
```
