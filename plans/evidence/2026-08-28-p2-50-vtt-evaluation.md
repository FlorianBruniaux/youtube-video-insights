# Évidence P2 : évaluation de 50 VTT

**Version du contrat :** `1.0`
**Statut du fichier :** `CONTRAT SUIVI, ÉVALUATION NON RENSEIGNÉE`
**Statut de gate :** `UNKNOWN`

Ce fichier matérialise le contrat P2. Il ne contient encore ni manifeste, ni résultat de recherche, ni jugement humain. Son existence ne permet aucune promotion produit.

## Règles de statut

- `PASS` exige un manifeste complet de 50 VTT, son hash, les requêtes, les résultats et des jugements humains explicites répondant au seuil adopté.
- `FAIL` exige les mêmes données et une décision explicite que le seuil n'est pas atteint.
- `UNKNOWN` est obligatoire si une donnée requise manque, si le manifeste diffère de celui évalué, ou si aucun jugement humain n'est renseigné.

Statut actuel : `UNKNOWN`. Aucun jugement humain n'est saisi.

## Identité du snapshot

| Champ | Valeur |
|---|---|
| Producteur P2-S1 | `À RENSEIGNER` |
| Branche | `À RENSEIGNER` |
| Commit Git | `UNKNOWN` |
| Worktree | `À RENSEIGNER` |
| Date de création du manifeste | `UNKNOWN` |

## Manifeste ordonné des 50 VTT

**Emplacement prévu :** `plans/evidence/2026-08-28-p2-50-vtt-manifest.tsv`
**État :** `NON RENSEIGNÉ`
**Hash SHA-256 du manifeste :** `UNKNOWN`
**Méthode de calcul :** `À RENSEIGNER`

Le manifeste à créer contient exactement 50 lignes ordonnées. Chaque ligne porte au minimum : rang, identité vidéo, langue, chemin VTT relatif et hash SHA-256 de la source. Toute modification du manifeste ou de son ordre impose une nouvelle évaluation et maintient ce statut à `UNKNOWN` jusqu'à revue humaine.

## Sujets et requêtes

**État :** `NON RENSEIGNÉ`.

Ajouter les sujets d'articles réellement envisagés et chaque requête dans l'ordre d'évaluation, avec le rang du manifeste concerné lorsque pertinent.

## Résultats de recherche

**État :** `NON RENSEIGNÉ`.

Pour chaque requête, enregistrer la commande ou le protocole, le résultat retourné, le `passage_id`, le texte ou extrait nécessaire à la revue, le timestamp et le lien YouTube vérifiable.

## Jugements humains

**État :** `AUCUN JUGEMENT SAISI`.

P2-S2 ajoute ici uniquement des jugements humains explicites, liés au hash du manifeste : auteur, date, requête, résultat évalué et verdict. Ne pas inférer un jugement depuis un test, une métrique ou un résultat technique.

## Seuil et décision

| Champ | Valeur |
|---|---|
| Seuil adopté | `UNKNOWN` |
| Méthode de calcul | `À RENSEIGNER` |
| Décision humaine | `AUCUNE` |
| Statut final | `UNKNOWN` |

## Handoff

P2-S1 complète puis versionne le manifeste et les résultats. Après fusion de son commit, P2-S2 consomme cet exact commit et hash, devient propriétaire de cette section de jugements et met à jour le statut. L'absence de commit, hash, données ou jugement humain conserve `UNKNOWN` et bloque uniquement promotion, exposition et recommandation produit.
