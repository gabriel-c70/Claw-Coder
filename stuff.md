# Hey there bet you did not know this is a RAG agent
### Let's go over what RAG really is in agentic workflows.
### RAG in simple terms is a way to feed huge chunks of text by breaking each word into a vector to an LLM cause the context window of an LLM is so small the solution was to create RAG.

## SO WHERE DO THESE VECTORS GO
### Well they go into a vector database like chromadb, Fiass-sth like that, and so many others so why do these vectors matter well once they are in the vector db they are considered as vector embeddings and when the user sends a prompt to an AI it carrys out something we call vector searching to find what is most similar to the user's prompt but how - this is possible through cosine similarity search which is finding the most similar meaning of the user's prompt compared to a vector embedding in the vector db.
### I dont want to make this search a boring explanation so this is the simplest explanation i can give you about RAG thank you.
[helo](https://cloud.google.com/discover/what-is-prompt-engineering)

````python
def hello():
    print('hello')
hello()
````