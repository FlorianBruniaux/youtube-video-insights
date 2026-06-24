# 20260223 - Florian Bruniaux - 7 mois, 1200 commits, 600 PRs, 50 releases : REX d'une équipe AI-first de 4 pe...

**Subject**: REX sur la construction d'une plateforme éducative AI-first en 7 mois avec une équipe de 4 personnes : 1200 commits, 600 PRs, 50 releases.

---

## Key points

- Priorité à la vélocité : livraison rapide d'un POC vers une V1 utilisable en s'appuyant sur une stack familière et des composants tiers.
- Adoption des LLMs (Claude, agents) comme accélérateur de développement et fonctionnalité produit, avec gestion fine du contexte mémoire.
- Architecture saine dès le départ : pattern router/repository/service réutilisé d'un squelette éprouvé pour tenir la qualité malgré la vitesse.
- Buy over build : intégrer des services prêts à l'emploi (auth, notifications, visio) plutôt que tout développer en interne.
- Industrialisation progressive : documentation, cheat-sheets, puis montée en charge collaborative avec des non-techniques devenus builders.

## Tools mentioned

| Tool | Context |
|---|---|
| Next.js | stack frontend principale |
| tRPC | couche API type-safe |
| Prisma | ORM base de données |
| Clerk | authentification en service tiers |
| Daily.co | visioconférence intégrée |
| Claude | LLM principal pour développement et fonctionnalités produit |
| Git | 1200 commits, 600 PRs, 50 releases en 7 mois |

## Actionable advice

- Réutiliser un squelette de projet maîtrisé pour démarrer vite, stack connue avec bonnes pratiques intégrées.
- Préférer l'intégration de services tiers (auth, notifications, visio) plutôt que tout développer en interne.
- Mesurer et gérer la mémoire des LLMs : purger régulièrement au-delà de 70% de contexte utilisé pour éviter les hallucinations.
- Découper le travail en petites PRs et releases fréquentes pour itérer rapidement et réduire le risque par merge.
- Fournir cheat-sheets et onboarding simple pour transformer des collègues non-techniques en contributeurs productifs.

## Notable quotes

> "7 mois, 1200 commits, 600 PRs, 50 releases."

> "On bombarde. Vélocité, vélocité."
