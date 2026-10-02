#!/usr/bin/env python3
"""Download ClassApp media for the configured student."""

import argparse
import getpass
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


AUTH_URL = "https://classapp.com.br/auth"
CARD_SELECTOR = '.ui.grid [data-action="open-media-item"]'
MODAL_SELECTOR = ".ui.modal.mediaItem"


def find_active_load_more_button(page):
    buttons = page.locator("button").filter(has_text=re.compile(r"carregar mais", re.I))
    for index in range(buttons.count()):
        button = buttons.nth(index)
        if button.is_visible() and button.is_enabled():
            return button
    return None


def validate_media_url(media_url):
    parsed_url = urllib.parse.urlparse(media_url)
    valid_host = parsed_url.hostname in {"classapp.com.br", "www.classapp.com.br"}
    valid_path = re.fullmatch(r"/entities/\d+/medias/?", parsed_url.path)
    if parsed_url.scheme != "https" or not valid_host or not valid_path:
        raise ValueError(
            "Informe a URL HTTPS da galeria no formato "
            "https://classapp.com.br/entities/ID/medias."
        )
    return media_url.rstrip("/")


def normalize_label(value):
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().split())


def authenticate(page, email, password):
    page.goto(AUTH_URL, wait_until="domcontentloaded")
    email_input = page.locator(
        'input[type="email"], input[name="email"], input[placeholder*="@"]'
    ).first
    email_input.wait_for(state="visible", timeout=15000)
    email_input.fill(email)
    page.get_by_role("button", name=re.compile(r"continue|continuar", re.I)).click()

    password_input = page.locator('input[type="password"]').first
    try:
        password_input.wait_for(state="visible", timeout=20000)
    except PlaywrightTimeoutError:
        print("O ClassApp não exibiu um campo de senha.")
        input("Conclua o login ou a verificação no navegador e pressione Enter: ")
        return

    password_input.fill(password)
    password_input.press("Enter")
    try:
        page.wait_for_url("**/entities/**", timeout=30000)
    except PlaywrightTimeoutError:
        print("Finalize qualquer etapa adicional de autenticação no navegador.")
        input("Pressione Enter quando o login estiver concluído: ")


def load_all_cards(page):
    cards = page.locator(CARD_SELECTOR)
    rounds = 0

    page.wait_for_function(
        "selector => document.querySelectorAll(selector).length > 0",
        arg=CARD_SELECTOR,
        timeout=30000,
    )

    while True:
        try:
            page.wait_for_function(
                """() => [...document.querySelectorAll('button')].some(button =>
                    /carregar mais/i.test(button.innerText) &&
                    button.getClientRects().length > 0 &&
                    !button.disabled && button.getAttribute('aria-disabled') !== 'true')""",
                timeout=20000 if rounds == 0 else 5000,
            )
        except PlaywrightTimeoutError:
            if rounds == 0:
                print("O botão 'Carregar mais' não apareceu ativo; confira a galeria no Chromium.")
            break

        button = find_active_load_more_button(page)
        if button is None:
            continue

        previous_count = cards.count()
        print(f"Clicando em 'Carregar mais' ({previous_count} itens carregados)...")
        button.scroll_into_view_if_needed()
        button.click()
        rounds += 1

        try:
            page.wait_for_function(
                """({selector, previousCount}) =>
                    document.querySelectorAll(selector).length > previousCount""",
                arg={"selector": CARD_SELECTOR, "previousCount": previous_count},
                timeout=30000,
            )
        except PlaywrightTimeoutError as error:
            if find_active_load_more_button(page) is None:
                break
            raise RuntimeError(
                "O botão 'Carregar mais' foi clicado, mas a quantidade de itens não aumentou. "
                "Os downloads foram cancelados para evitar um backup incompleto."
            ) from error

        print(f"Itens carregados: {cards.count()} (página {rounds})")

    return cards


def wait_for_gallery_confirmation(page, cards):
    while True:
        confirmation = input(
            f"Confira a galeria no Chromium ({cards.count()} itens). "
            "Digite S e pressione Enter para iniciar os downloads: "
        )
        if confirmation.strip().lower() not in {"s", "sim"}:
            print("Download cancelado. O Chromium continuará aberto.")
            return False

        if find_active_load_more_button(page) is not None:
            print("Ainda há um botão 'Carregar mais' ativo. Clique nele no Chromium e confira novamente.")
            continue

        return True


def modal_media_urls(modal):
    return modal.evaluate(
        """modal => {
            const urls = new Set();
            const add = value => {
                if (value && (value.startsWith('https://') || value.startsWith('http://'))) urls.add(value);
            };

            for (const node of modal.querySelectorAll('img, video, source')) {
                add(node.currentSrc);
                add(node.src);
                add(node.getAttribute('data-src'));
            }

            for (const link of modal.querySelectorAll('a[href]')) {
                const url = link.href;
                if (link.hasAttribute('download') || /[.](jpg|jpeg|png|gif|webp|mp4|mov|webm)(?:$|[?#])/i.test(url)) {
                    add(url);
                }
            }

            return [...urls];
        }"""
    )


def file_extension(url, content_type=""):
    extension = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).suffix
    if extension:
        return re.sub(r"[^.a-zA-Z0-9]", "", extension).lower()

    guessed = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
    }
    return guessed.get(content_type.split(";")[0].lower(), ".bin")


def download_url(url, output_dir, index, cookies, user_agent, media_url):
    cookie_header = "; ".join(f"{item['name']}={item['value']}" for item in cookies)
    headers = {"User-Agent": user_agent, "Referer": media_url}
    if cookie_header:
        headers["Cookie"] = cookie_header

    request = urllib.request.Request(url, headers=headers)
    temporary_path = None
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = response.headers.get("Content-Type", "")
            extension = file_extension(url, content_type)
            parsed_name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
            stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(parsed_name).stem).strip("._")
            filename = f"{index:04d}_{stem or 'media'}{extension}"
            destination = output_dir / filename
            temporary_path = destination.with_suffix(destination.suffix + ".part")

            with temporary_path.open("wb") as media_file:
                while chunk := response.read(1024 * 1024):
                    media_file.write(chunk)

        temporary_path.replace(destination)
        return destination
    except (urllib.error.URLError, TimeoutError, OSError):
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
        return None


def scrape(output_dir, media_url, school_name, student_name):
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise RuntimeError(
            f"A pasta de destino já contém arquivos: {output_dir}. "
            "Escolha uma pasta vazia para proteger o backup existente."
        )

    downloaded_urls = set()

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=False)
        except Exception as error:
            raise RuntimeError(
                "O Chromium não iniciou. No WSL, instale as dependências com "
                "`sudo .venv/bin/playwright install-deps chromium` e tente novamente."
            ) from error

        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        email = input("Email do ClassApp: ").strip()
        if not email:
            raise ValueError("O email do ClassApp é obrigatório.")
        password = getpass.getpass(f"Senha do ClassApp para {email}: ")
        try:
            authenticate(page, email, password)
        finally:
            password = None

        page.goto(media_url, wait_until="domcontentloaded")
        if page.locator('input[type="email"], input[type="password"]').count():
            raise RuntimeError("Login não concluído. Verifique o navegador e tente novamente.")

        page.locator(".ui.grid").wait_for(state="visible", timeout=30000)
        image_alts = page.locator("img").evaluate_all(
            "images => images.map(image => image.alt.trim()).filter(Boolean)"
        )
        normalized_alts = {normalize_label(alt) for alt in image_alts}
        if normalize_label(school_name) not in normalized_alts:
            raise RuntimeError(f"A escola esperada não foi encontrada: {school_name}")
        normalized_student = normalize_label(student_name)
        if normalized_student not in normalized_alts:
            first_name = normalized_student.split()[0] if normalized_student else ""
            likely_profiles = sorted(
                {
                    alt
                    for alt in image_alts
                    if normalize_label(alt).startswith(first_name)
                    and normalize_label(alt) != normalize_label(school_name)
                }
            )
            detail = (
                f" Nome(s) semelhante(s) exibido(s): {', '.join(likely_profiles)}."
                if likely_profiles
                else " Nenhum perfil com esse primeiro nome foi identificado na página."
            )
            raise RuntimeError(
                f"A aluna esperada não foi encontrada: {student_name}.{detail} "
                "Confira se --media-url aponta para a galeria correta e use o nome exibido no perfil."
            )

        cards = load_all_cards(page)
        print(f"Galeria pronta para conferência: {cards.count()} itens.")
        if not wait_for_gallery_confirmation(page, cards):
            print("Feche a janela do Chromium quando terminar.")
            page.wait_for_event("close")
            return

        total = cards.count()
        print(f"Processando {total} itens em {output_dir.resolve()}...")
        user_agent = page.evaluate("() => navigator.userAgent")

        for index in range(total):
            card = page.locator(CARD_SELECTOR).nth(index)
            try:
                card.scroll_into_view_if_needed()
                card.click(timeout=10000)
                modal = page.locator(MODAL_SELECTOR)
                modal.wait_for(state="visible", timeout=10000)
                try:
                    page.wait_for_function(
                        """() => {
                            const modal = document.querySelector('.ui.modal.mediaItem');
                            return modal && modal.querySelector('img, video, source');
                        }""",
                        timeout=10000,
                    )
                except PlaywrightTimeoutError:
                    pass

                for url in modal_media_urls(modal):
                    if url in downloaded_urls:
                        continue
                    downloaded_urls.add(url)
                    destination = download_url(
                        url,
                        output_dir,
                        index + 1,
                        context.cookies(url),
                        user_agent,
                        media_url,
                    )
                    if destination:
                        print(f"Baixado: {destination.name}")
                    else:
                        print(f"Falha ao baixar o item {index + 1}.")
            except PlaywrightTimeoutError:
                print(f"Não foi possível abrir o item {index + 1}.")
            finally:
                page.keyboard.press("Escape")
                try:
                    page.locator(MODAL_SELECTOR).wait_for(state="hidden", timeout=3000)
                except PlaywrightTimeoutError:
                    pass

            if (index + 1) % 25 == 0:
                print(f"Progresso: {index + 1}/{total}")

        print(f"Concluído. {len(downloaded_urls)} URLs de mídia encontradas.")
        print("O Chromium permanecerá aberto. Feche a janela para encerrar o programa.")
        page.wait_for_event("close")


def main():
    parser = argparse.ArgumentParser(description="Baixa as mídias da galeria do ClassApp.")
    parser.add_argument(
        "--media-url",
        required=True,
        help="URL da galeria no ClassApp",
    )
    parser.add_argument(
        "--student-name",
        required=True,
        help="Nome completo da aluna, para validar o perfil e nomear a pasta",
    )
    parser.add_argument(
        "--school-name",
        required=True,
        help="Nome da escola exibido no ClassApp",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Pasta de destino vazia (no WSL, use /mnt/c/Roberto/Classapp/...)",
    )
    args = parser.parse_args()

    try:
        media_url = validate_media_url(args.media_url)
        scrape(args.output, media_url, args.school_name, args.student_name)
    except KeyboardInterrupt:
        print("Download interrompido.", file=sys.stderr)
    except Exception as error:
        print(f"Erro: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()