import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

const STEP_PAUSE_MS = 2000

async function removeTempTestDir() {
  const homeDir = os.homedir()
  const targetDir = path.join(homeDir, 'projects', 'temp-test')
  await fs.rm(targetDir, { recursive: true, force: true })
}

async function sendUserMessage(page: Page, text: string) {
  const composer = page.getByLabel('输入消息')
  await composer.fill(text)
  await composer.press('Enter')
}

async function stepPause(page: Page) {
  await page.waitForTimeout(STEP_PAUSE_MS)
}

test.beforeEach(async () => {
  // 每次跑之前清掉上次留下的测试目录，避免误判。
  await removeTempTestDir()
})

test('端到端：对话 + bash 工具链路', async ({ page }) => {
  await page.goto('/')
  await stepPause(page)

  // 1) 测试AI能不能看到初始指令（init_prompts.py）里面设定的工作目录
  await sendUserMessage(page, 'hi，你知道现在的工作目录是什么吗？另外，我叫小明')
  const lastAssistantText = page
    .locator('article')
    .filter({ hasText: 'assistant' })
    .last()
    .locator('pre')
    .last()
  await expect(lastAssistantText).toContainText("x-space")
  await stepPause(page)

  // 2) 让 AI 查看当前时间
  // 这里把“必须调用 bash”写得非常明确，否则真实模型可能回复初始指令里面提供的时间
  await sendUserMessage(page, '请调用 bash 工具执行命令：date')
  const timeToolCard = page.locator('article').filter({ hasText: 'Tool' }).last()
  await expect(timeToolCard.getByText('bash')).toBeVisible()
  await expect(timeToolCard.getByText('调用完成')).toBeVisible()
  await stepPause(page)

  // 3) 让 AI 把工作目录切换到 ~/projects/temp-test
  await sendUserMessage(
    page,
    '请调用 bash 工具执行命令：mkdir -p ~/projects/temp-test && cd ~/projects/temp-test && pwd',
  )
  const cdToolCard = page.locator('article').filter({ hasText: 'Tool' }).last()
  await expect(cdToolCard.getByText('bash')).toBeVisible()
  await expect(cdToolCard.getByText('调用完成')).toBeVisible()
  await stepPause(page)

  // 4) 让 AI 创建 test.txt 写入 hello world
  await sendUserMessage(
    page,
    "请使用apply_patch来创建一个文件叫test.txt并在里面写入`hello world!`",
  )
  const writeToolCard = page.locator('article').filter({ hasText: 'Tool' }).last()
  await expect(writeToolCard.getByText('apply_patch')).toBeVisible()
  await expect(writeToolCard.getByText('调用完成')).toBeVisible()
  await stepPause(page)

  const homeDir = os.homedir()
  const filePath = path.join(homeDir, 'projects', 'temp-test', 'test.txt')
  await expect
    .poll(async () => fs.readFile(filePath, 'utf8'))
    .toContain('hello world!')
  await stepPause(page)

  await sendUserMessage(
      page,
      "你还记得我叫什么名字吗？",
  )
  const lastAssistantText2 = page
      .locator('article')
      .filter({ hasText: 'assistant' })
      .last()
      .locator('pre')
      .last()
  await expect(lastAssistantText2).toContainText("小明")
  await stepPause(page)
})
