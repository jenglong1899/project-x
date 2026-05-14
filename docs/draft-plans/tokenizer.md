做tokenizer出来，要支持好几个模型，目前首先考虑支持deepseek和qwen

Transformer 库是有相关的功能的，我们要用到那个。关于网络问题，到时候我们在客户机上装个 VPN 就行了。

我们得做一个abstract class出来，目前的接口想法如下：

计算当前 token 占上下文窗口的百分比，如果返回20，就代表占了20%。
calculate_token_percentage(model:str,messages:dict[str,Any])->int

calculate_text_token(model:str,text:str)->int

判断模型的时候，我们检查关键字就行了，比如 'deepseek' in model, 'qwen' in model

测试的思路就是，我们发送一个正式的 llm API 调用，然后看它返回结果，看它返回结果里面它那个token是多少，然后再和我们本地的那个实现做个对比。
