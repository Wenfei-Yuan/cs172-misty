---
name: 'Query Repo'
description: 'Instructions for answering questions about an existing repo'
---
# Ask about an existing repo
**DO NOT TRY TO COMMIT CHANGES TO GITHUB**
**DO NOT WRITE SPAM FILES INTO THE REPO**
**DO NOT USE SUDO**
inputs:
input 1: target repo, questions
input 2: important files

**read through this entire file and follow the instructions carefully**. 
Before answering any questions, always, first read the following files @.github/instructions/repo_info (REFER AS KEY MD FILES):
1. codebase_overview.md
2. scripts_overview.md
3. update_logs.md
4. known_issues.md
Understand them, and keep them inside the memory. 
The main agent should go through all files and scripts inside the repo and get a detailed understanding. 


then, for answering any questions to an existing codebase:
1. if important files are specified, the main agent should read through the important files, and understand them. then combine the understood knowledge with the key md files.
2. Then, the main agent should decide what are the most relevant codes, scripts, files, and functionalities to the questions, and create a list of **BRIEF** [important information]. 
**Step 3, step 4, and step 5 should be done in parallel**
3. the main agent creates a subagent (code agent, focus mode), pass the questions and the [important information] list to the subagent. The subagent should also read through the key md files. Based on the [important information] list and the repo structure based on the key md files, read every file, function, script mentioned in the [important information] list. Then, the subagent should answer the questions accordingly, and report the answers back to the main agent (Refer as, [answers 1]). 
4. the main agent creates a subagent (code agent, broad mode), pass the questions and the [important information] list to the subagent. The subagent should also read through the key md files. Based on the [important information] list and the repo structure based on the key md files, the subagent should go through the repo pipeline and read all scripts that associated with the questions, and then read all upstream and downstream scripts that associated with the questions along the codebase workflow. Then, the subagent should answer the questions accordingly, and report the answers back to the main agent (Refer as, [answers 2]). 
5. the main agent creates a subagent (code agent, free mode), pass the questions to the subagent. The subagent should also read through the key md files. Based on the questions and repo information from the key md files, the subagent should decide what files and scripts to read and check to get 100% correct answers. Then, the subagent should answer the questions accordingly, and report the answers back to the main agent (Refer as, [answers 3]). 
6. the main agent should read through all three answers ([answers 1], [answers 2], and [answers 3]), understand each of them, combines the advantages of each answer, reject the redundant or incorrect parts of each answer, and draft precise and 100% correct answers to answer questions in bullet points. 
7. the main agent creates a subagent (devil's advocate), pass the drafted answers and the original questions to the subagent. The subagent should read through the key md files and the [important information] list, then critically challenge the drafted answers — looking for factual errors, unsupported claims, missing edge cases, or contradictions with the codebase. The subagent reports any flaws back to the main agent. The main agent incorporates valid criticisms and finalizes the answers.
8. the main agent should summarize the questions and answers in the following format, for question and answer pair:
{=============================Q&A: (fill an Q&A ID here, simply use last Q&A ID + 1)===============================}
Question: (fill a one sentence summary of the question here.)
Answer: (fill a brief but precise summary of the answer in bullet points here.)
Then the main agent should write it to past_Q&A.md

