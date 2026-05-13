你做一次编辑，你只需要知道最后是不是编辑成功了。
我们现在这个工具在编辑之后会返回一个unified diff，AI 根据这个来判断有没有成功。
在编辑工具调用之后，系统自动唤起一个ai去查看diff，成功返回ok。。。
edit_tool -> edit -> diff -> AI -> success -> return ok
                                |-> fail -> return fail?
算了，这也太复杂了。
