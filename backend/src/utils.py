def is_deepseek_reasoner(model:str)->bool:
    return model.__contains__("deepseek-reasoner")
