'''
This file stores some necessary constants.
You may modify some constants according to your needs.
'''
# For common use.
from register import ToolRegister
toolreg = ToolRegister()

# define how the agent will return a proposal.
from pydantic import BaseModel, Field
from typing import Optional
class AgentResponse(BaseModel):
    Purpose: str = Field(description = "The purpose of the last action from the user.")
    Thoughts: str = Field(description = "Your thoughts on the user's actions.")
    Proactive_Task: Optional[str] = Field(description = "a candidate task that you generate to help the user, or an empty list if the user need no help.")
    Response: Optional[str] = Field(description = "The string you use to inform the user about your assistance if you propose a task.")
    Operation: Optional[str] = Field(description = "A tool call string that follows a certain format given.")

