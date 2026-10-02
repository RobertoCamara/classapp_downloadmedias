# Backup de mídias do ClassApp

Baixa fotos e vídeos da galeria indicada pelo usuário. O script usa um navegador temporário, percorre o botão **Carregar mais** até não haver novos itens e salva os arquivos localmente.

## Instalação

Em Debian/Ubuntu, instale `python3-venv` caso a criação do ambiente virtual informe que `ensurepip` não está disponível:

```bash
sudo apt install python3-venv
```

Depois, na pasta do projeto:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
sudo .venv/bin/playwright install-deps chromium
```

## Execução

```bash
python3 download_media.py \
	--media-url "https://classapp.com.br/entities/ID_DA_ALUNA/medias" \
	--student-name "Nome Completo" \
	--school-name "Nome da Escola" \
	--output "/mnt/c/Roberto/Classapp/Nome Completo"
```

O email é solicitado no terminal e a senha é solicitada sem exibição. Nenhuma credencial é gravada em arquivo. O navegador e seus cookies são temporários e não são persistidos. Se o ClassApp solicitar uma etapa adicional de autenticação, conclua-a no navegador.

Antes de baixar, confira a quantidade e a galeria no Chromium. Digite `S` no terminal para confirmar; se ainda houver um botão **Carregar mais** ativo, clique nele e confira novamente. Ao terminar, o programa manterá o navegador aberto até você fechar a janela.

Informe todos os parâmetros em cada execução e escolha uma pasta nova e vazia sob `C:\Roberto\Classapp` (montada no WSL em `/mnt/c/Roberto/Classapp`). O script recusa iniciar se a pasta de saída já contiver arquivos, protegendo backups anteriores. A URL deve ser a da galeria da aluna, no formato `/entities/ID/medias`.