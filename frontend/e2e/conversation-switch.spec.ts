import { expect, test } from '@playwright/test'

test('会话列表与切换会话（断开重连）', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByText(/WebSocket：open/)).toBeVisible()

  const input = page.getByPlaceholder('输入消息，回车发送；Shift+Enter 换行。')
  const sendButton = page.getByRole('button', { name: '发送' })
  const newConversationButton = page.getByRole('button', { name: '新会话' })

  await input.fill('会话A')
  await sendButton.click()

  await expect(page.getByText(/生成中/)).toBeVisible()
  await expect(newConversationButton).toBeDisabled()
  await expect(page.getByText('（mock 回复）')).toBeVisible()
  await expect(page.getByText(/生成中/)).not.toBeVisible()

  const entryA = page.getByRole('button', { name: /会话A/ })
  await expect(entryA).toBeVisible()

  await newConversationButton.click()

  await input.fill('会话B')
  await sendButton.click()
  await expect(page.getByText('（mock 回复）')).toBeVisible()
  await expect(page.getByText(/生成中/)).not.toBeVisible()

  const entryB = page.getByRole('button', { name: /会话B/ })
  await expect(entryB).toBeVisible()

  await entryA.click()

  await expect(page.getByRole('main').getByText('会话A')).toBeVisible()
  await expect(page.getByText('（mock 回复）')).toBeVisible()

  await input.fill('继续A')
  await sendButton.click()
  await expect(page.getByText('（mock 回复）')).toBeVisible()
})
