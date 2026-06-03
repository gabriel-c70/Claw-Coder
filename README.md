# Claw Coder

![claw-coder logo](Claw-Coder/logo3.png)

### Claw coder is a local first AI agent that turns local coding small LLMs into powerful AI agents that actually work here is how:
#### Claw coder has access to knowledge graph which means it can ingest files and directories and actually map them and understand what each part does to the other without even needing powerful GPUs and the knowledge graph is lightweight which means it runs completely on you laptop

![claw chat displayed](Claw-Coder/Screenshot 2026-06-03 at 11.32.01.png)

> - Claw coder has access to tree-sitter + RAG which is put in place just for the coding purpose only but the RAG is designed for both functionalities like for code and documents which means the local model can actually map relationships precisely with the help of knowledge graphs which is a powerful combination
> 
> 
> - Claw coder also has access to tools the elevate its power with real codebases:
## Tools include:

---
- Docker coder execution: Giving an AI agent docker code execution does not only improve coding performance and reasoning but also enables it to execute broken and working code all in isolated environment without destroying your venv.
---
- Search tools: Local AI and all of LLMs in general can't reason beyond their trained data which can lead to hallucinations but when given access to a search tool the hallucinations drop dramatically by up to 70%+ because it now has up_to_date information.
---
- Run tests: LLMs in general not only the local ones can write 1000s of lines of code that do not make sense from even the top to the last line but when given a test tool they can test their code and see where they went wrong and claw-coder is actually good with this coz it can even test html and css code and actually see the output on the web.
---
  - git tools: Not considering git for AI agents can look like something easy to slide and leave on the side because it looks useless but giving git to AI agents is not a luxury necessity but it enables the AI agent to check what changed and where and why and actually be a full AI engineer on your laptop on your lap completely local.
---
  - file tools: These are actually the tools that make an agent code in a real file and clear mistakes and these tools really help the agent just do its work outside the terminal.
---
### These are powerful tools but isn't it better to just use existing agents and configure them?:
### Well this is a good question but the is something to point out:

---

````
|______________________________________________________________________________________________________________________________________________________|
|__________|Runs locally|Repository understanding|Gives performance without compromaising security and privacy  |Code reasoning locally                |
|Cursor    |No          |Yes                     |No                                                            |No                                    |
|Codex     |No          |Yes                     |No                                                            |No                                    |
|Claw-coder|Yes         |Yes                     |Yes                                                           |Yes                                   | 
|Claude    |No          |Yes                     |No                                                            |No                                    |
|__________|____________|________________________|______________________________________________________________|______________________________________|
````
- ### Caution: Claw-Coder is indeed something else, but it is not perfect it can make mistakes and mess up don't be too open to claw-coder with your environment.
- ### But this has been thought of at file stage the AI has a directory called workspace where it works from without destroying your file structure.

---

### Now time to install and have fun with claw-coder.

---
## Install

---

From this directory:

```bash
npm install -g .
claw setup
```
---
For development, use a symlink instead:

```bash
npm link
claw setup
```
---
- `claw setup` installs the Python dependencies from 
  - `requirements.txt`. You also
need Ollama running for chat, embeddings, and vector RAG:
---
```bash
ollama serve
claw <model>
claw <chat model> <embedding modal>
```
---
## Use

```bash
claw doctor
claw languages
claw ingest .
claw graph "tree_sitter imports" --depth 2<img width="1352" height="651" alt="Screenshot 2026-06-03 at 11 30 40" src="https://github.com/user-attachments/assets/75ca5440-47bc-457e-8fdf-f67967645b62" />claw search "where is graph reranking implemented?" --top-k 5
claw chat
```
- This is a screenshot of claw --help with all the commands displayed

![claw --help displayed](Claw-Coder/Screenshot 2026-06-03 at 11.48.53.png)

---
Useful options:

```bash
claw ingest ./src --no-vector-rag
claw search "authentication flow" --graph ./my_graph.json --db ./rag_db
claw graph "calls run_terminal" --top-k 10 --depth 3
```

### This is a credit based product so the powerful tools like docker execution and so much more while be credit based.

- To check your usage you run
```bash
claw usage
````

![claw usage displayed](Claw-Coder/Screenshot 2026-06-03 at 11.44.12.png)

---
You can also use the longer binary name:

```bash
claw-coder doctor
```
---
> Source Code: [claw-coder](https://github.com/gabriel-c70/Claw-Coder.git) 
>
> You can also contribute and make [claw-coder](https://github.com/gabriel-c70/Claw-Coder.git) the best AI agent ever created by just contributing a line of code.
---
