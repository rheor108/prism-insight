"""mcp-agent compatibility interface; every generation stays on Codex."""
from openai.types.chat import ChatCompletionMessage
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from prism_core.codex_subscription import run_stage, run_with_registry


class SubscriptionLLM(OpenAIAugmentedLLM):
    stage = None
    variant = None

    async def generate_str(self,message,request_params=None):
        return await run_with_registry(self.stage,self.instruction,message,
                                       getattr(self.agent,'server_names',()), variant=self.variant,
                                       tool_filter=getattr(request_params,'tool_filter',None))

    async def generate(self,message,request_params=None):
        return [ChatCompletionMessage(role='assistant',content=await self.generate_str(message,request_params))]

    async def generate_structured(self,message,response_model,request_params=None):
        text = await run_with_registry(self.stage,self.instruction,message,
                    getattr(self.agent,'server_names',()),response_model=response_model, variant=self.variant,
                    tool_filter=getattr(request_params,'tool_filter',None))
        return response_model.model_validate_json(text)


def llm_for(stage, *, variant=None):
    return type('Codex_'+stage,(SubscriptionLLM,),{'stage':stage,'variant':variant})


def summary_factory(optimizer,evaluator):
    def factory(*,agent,**kwargs):
        if agent is optimizer:
            stage = 'telegram_summary'
        elif agent is evaluator:
            stage = 'telegram_evaluator'
        else:
            raise ValueError('Unknown summary agent')
        return llm_for(stage)(agent=agent,**kwargs)
    return factory
