Create a coding agent which will be used to identify bugs in the system. The input for this agent will be plain english and based on it the agent will find out the bug and fix it. Don't use langgraph for this agent. For LLM use the groq as mentioned in the env file.

This coding agent will accept the input as plain english from cli and based on it LLM will identify which tools to call and then fix it. The complete agent should have maximum of 3 retries to avoid the token

For this create multiple tools

tool 1: search the complete repository to to find files related to a bug
tool 2: find withing files the lines related to the bug
tool 3: replace the file related to the bug

For debugging, make sure to add print statements in all places so that complete flow is logged

In order to test it, create a boiler plate python file which will have basic math operations. In this file, don't add a bug, I will add the bug myself

Also don't write any testcases or do testing, i will do that myself

For example:
If in cli the prompt is: my multiply is not working

Resolution:
LLM will call tool 1 to find out all files where multiply is used
tool 2 will find the lines related to the multiply
LLM will find the bug in those lines
tool 3 will fix that bug by replacing the code
