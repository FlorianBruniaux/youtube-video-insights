# Prompts Claude Code et Codex

Nommez le skill ou l’agent dans le prompt. YT Insights conserve un routage explicite pour éviter qu’une demande générale sur YouTube déclenche une acquisition ou une recherche locale par erreur.

## Prévisualiser une chaîne avant acquisition

```text
Utilise youtube-acquire pour prévisualiser cette chaîne YouTube : URL.
Ne télécharge rien avant ma confirmation. Donne-moi le nombre de vidéos retenues,
les exclusions, la langue demandée et le répertoire cible.
```

## Acquérir une vidéo précise

```text
Utilise youtube-acquire pour ajouter cette vidéo à mon corpus local : URL.
Commence par doctor et le dry-run. Si la vidéo est bien identifiée et que les
prérequis passent, lance l’acquisition puis rapporte selected, transcripts_ready,
insights_ready et les erreurs éventuelles.
```

## Rechercher des preuves pour un article

```text
Utilise youtube-research pour chercher dans mon corpus les passages qui répondent
à cette question : QUESTION. Limite-toi à 10 passages. Pour chaque résultat,
donne le titre, la chaîne, un extrait borné, le timestamp, le lien direct et la
langue. Sépare les preuves directes de ton interprétation et indique les limites
de couverture du corpus.
```

## Comparer deux chaînes

```text
Utilise youtube-corpus-researcher pour comparer les positions de CHAÎNE_A et
CHAÎNE_B sur SUJET. Cherche les accords, désaccords et exemples concrets. Chaque
affirmation doit pointer vers un passage horodaté. Signale les questions que le
corpus ne permet pas de trancher.
```

## Préparer un dossier de rédaction

```text
Utilise youtube-corpus-researcher pour préparer un dossier sourcé sur SUJET pour
un article de blog. Regroupe les idées récurrentes, les désaccords, les exemples
et les citations utiles. Pour chaque élément, conserve le titre, la chaîne,
l’extrait, le timestamp et le lien YouTube. Termine par les angles encore sans
preuve dans le corpus.
```

## Retrouver la source d’une citation

```text
Utilise youtube-research pour retrouver la source de cette phrase ou de cette
idée dans mon corpus : EXTRAIT. Donne uniquement les correspondances étayées,
avec le passage, le timestamp et le lien direct. Si la recherche ne trouve rien,
dis-le sans proposer un résultat voisin comme preuve.
```

## Exporter la matière d’une vidéo

```text
Utilise youtube-export pour exporter la vidéo URL_OU_ID en Markdown. N’appelle
aucun LLM, ne remplace aucun fichier existant et retourne le chemin exact, la
langue, le format et le source_sha256.
```

## Enchaîner acquisition, recherche et export

```text
Travaille en trois étapes explicites. Utilise d’abord youtube-acquire pour
prévisualiser URL et attends ma confirmation si la source contient plusieurs
vidéos. Après acquisition confirmée, utilise youtube-research pour répondre à
QUESTION avec des passages horodatés. Exporte seulement les vidéos que je
sélectionnerai ensuite avec youtube-export.
```

Cette dernière recette respecte les frontières d’écriture. Le chercheur reste en lecture seule, tandis que l’acquisition et l’export s’exécutent dans la session principale.
