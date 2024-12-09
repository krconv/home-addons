<h1 align="center">
  <br>
  <a href="https://tandoor.dev"><img src="https://github.com/vabene1111/recipes/raw/develop/docs/logo_color.svg" height="256px" width="256px"></a>
  <br>
  Tandoor Recipes
  <br>
</h1>

<h4 align="center">The recipe manager that allows you to manage your ever growing collection of digital recipes.</h4>

<p align="center">
<a href="https://github.com/vabene1111/recipes/actions" target="_blank" rel="noopener noreferrer"><img src="https://github.com/vabene1111/recipes/workflows/Continuous%20Integration/badge.svg?branch=master" ></a>
<a href="https://github.com/vabene1111/recipes/stargazers" target="_blank" rel="noopener noreferrer"><img src="https://img.shields.io/github/stars/vabene1111/recipes" ></a>
<a href="https://github.com/vabene1111/recipes/network/members" target="_blank" rel="noopener noreferrer"><img src="https://img.shields.io/github/forks/vabene1111/recipes" ></a>
<a href="https://discord.gg/RhzBrfWgtp" target="_blank" rel="noopener noreferrer"><img src="https://badgen.net/badge/icon/discord?icon=discord&label" ></a>
<a href="https://hub.docker.com/r/vabene1111/recipes" target="_blank" rel="noopener noreferrer"><img src="https://img.shields.io/docker/pulls/vabene1111/recipes" ></a>
<a href="https://github.com/vabene1111/recipes/releases/latest" rel="noopener noreferrer"><img src="https://img.shields.io/github/v/release/vabene1111/recipes" ></a>
<a href="https://app.tandoor.dev/accounts/login/?demo" rel="noopener noreferrer"><img src="https://img.shields.io/badge/demo-available-success" ></a>
</p>

<p align="center">
<a href="https://tandoor.dev" target="_blank" rel="noopener noreferrer">Website</a> •
<a href="https://docs.tandoor.dev/install/docker/" target="_blank" rel="noopener noreferrer">Installation</a> •
<a href="https://docs.tandoor.dev/" target="_blank" rel="noopener noreferrer">Docs</a> •
<a href="https://app.tandoor.dev/accounts/login/?demo" target="_blank" rel="noopener noreferrer">Demo</a> •
<a href="https://discord.gg/RhzBrfWgtp" target="_blank" rel="noopener noreferrer">Discord</a>
</p>

![Preview](docs/preview.png)

## Core Features
- 🥗 **Manage your recipes** - Manage your ever growing recipe collection
- 📆 **Plan** - multiple meals for each day
- 🛒 **Shopping lists** - via the meal plan or straight from recipes
- 📚 **Cookbooks** - collect recipes into books
- 👪 **Share and collaborate** on recipes with friends and family

## Made by and for power users

- 🔍 Powerful & customizable **search** with fulltext support and [TrigramSimilarity](https://docs.djangoproject.com/en/3.0/ref/contrib/postgres/search/#trigram-similarity)
- 🏷️ Create and search for **tags**, assign them in batch to all files matching certain filters
- ↔️ Quickly merge and rename ingredients, tags and units
- 📥️ **Import recipes** from thousands of websites supporting [ld+json or microdata](https://schema.org/Recipe)
- ➗ Support for **fractions** or decimals
- 🐳 Easy setup with **Docker** and included examples for **Kubernetes**, **Unraid** and **Synology**
- 🎨 Customize your interface with **themes**
- 📦 **Sync** files with Dropbox and Nextcloud

## All the must haves

- 📱Optimized for use on **mobile** devices
- 🌍 localized in many languages thanks to the awesome community
- 📥️ **Import your collection** from many other [recipe managers](https://docs.tandoor.dev/features/import_export/)
- ➕ Many more like recipe scaling, image compression, printing views and supermarkets

This application is meant for people with a collection of recipes they want to share with family and friends or simply
store them in a nicely organized way. A basic permission system exists but this application is not meant to be run as
a public page.

## Upstream Updates
When there is a new version of Tandoor, follow these steps.

 1. Update `VERSION` argument in [Dockerfile](./Dockerfile)
 2. Update [Dockerfile](./Dockerfile) steps if they were modified upstream
 3. Update `version` in [config.yaml](./config.yaml)
