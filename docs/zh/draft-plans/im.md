# 接入飞书、slack
假设你在一个工作群里面艾特一个真人，按照常理，你要的其实只是他最终的结果，如果他全程在群里面说自己的工作过程，挺挤占空间的。
所以实现方式是：

tool: send_msg_to_im_platform(platform_name:str,msg:str)
（当然这只是个最初步的函数签名，肯定还有其他字段）

用户在群里艾特 AI ，或者给agent个人账户发消息后，系统发送一个 steer msg:
```text
<im from="some_group_or_some_people">
(...自从上一次艾特以来的所有消息...) / 用户发送了新消息
@bot 请去做xxx
</im>
```

边界情况：“自从上一次艾特以来的所有消息”可能会超过上下文长度。消息最大长度为10%，然后AI要做摘要，然后继续接收剩下的

ai在完成工作后调用send_msg_to_im_platform来发消息给用户。

如果用户想看工作过程，那么应该在 Web 端查看，而不是在im里面看

agent没事做的时候，就查看一下群聊 get_group_chat_msg_since_last_read(im_platform:str,group_id:str)
能否获得群聊中每个消息的准确发送时间（精准到毫秒？），记录一下最后一次读的消息，其发送时间是多少。
