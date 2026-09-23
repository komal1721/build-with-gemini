# My agent: smart-pantry
One-liner: A conversational agent that helps home cooks manage their kitchen with a catalog of pantry items.

Tool coverage:
- Memory: User dietary preferences, allergies, and favorite cuisines
- Tools: Pantry lookup (`list_pantry_items`), item addition (`add_pantry_item`), weather & time lookups
- Catalog/UI: Collection of pantry items rendered as structured cards/tables
- Image gen: n/a
- Sandbox: n/a

Core rails (everyone): memory, tools, eval, deploy, frontend
My stretch menu (pick later): Firestore storage, A2UI cards
First eval question: "What items do I have in my pantry that are expiring soon?"
