#!/usr/bin/env python3
"""Texas Hold'em sin límite, 2–10 jugadores, navegador móvil + TCP, biblioteca estándar.

Ejecutar: python3 poker_lan.py
Interfaz de escritorio anterior: python3 poker_lan.py --desktop
Pruebas:  python3 poker_lan.py --test
Servidor sin ventana: python3 poker_lan.py --server --port 5050 --pin amigos

Dinero virtual: saldo inicial S/ 10,000.00 (configurable), ciegas S/ 0.10 / 0.20.
El botón «Apostar / subir a» indica el importe TOTAL en soles de esa ronda.
Conectarse a la IP LAN del anfitrión; usar 127.0.0.1 para pruebas en un equipo.
No usa Bluetooth. No requiere pip. Python 3.10+; Tkinter solo para --desktop.
Red de confianza: protocolo TCP con clave compartida, sin cifrado TLS.
No usa dinero real, persistencia en disco ni servicios externos.
"""

import argparse
import itertools
import json
import os
import queue
import random
import secrets
import selectors
import socket
import sys
import threading
import time
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import urlsplit
from collections import Counter, deque
from dataclasses import dataclass, field


STACK, SB, BB, MAX_PLAYERS, TURN_SECONDS = 1000000, 10, 20, 10, 60
RANKS = "23456789TJQKA"
SUITS = "cdhs"
SYMBOLS = dict(zip(SUITS, "♣♦♥♠"))
HAND_NAMES = ["carta alta", "pareja", "doble pareja", "trío", "escalera",
              "color", "full house", "póker", "escalera de color"]


def money(cents, signed=False):
    sign = "−" if cents < 0 else "+" if signed and cents > 0 else ""
    cents = abs(cents)
    return f"{sign}S/ {cents // 100:,}.{cents % 100:02d}"


def parse_money(text):
    """Solo céntimos enteros: nunca usar float para saldos o apuestas."""
    text = str(text).strip().replace(",", ".")
    if len(text) > 16:
        raise ValueError("Importe demasiado grande.")
    try:
        value = Decimal(text)
        if not value.is_finite() or value < 0 or value > Decimal("1000000000"):
            raise ValueError("Introduce un importe entre 0 y 1000000000 soles virtuales.")
        cents = value * 100
        if cents != cents.to_integral_value():
            raise ValueError("Usa como máximo dos decimales, por ejemplo 0.10 o 2.50.")
        return int(cents)
    except InvalidOperation:
        raise ValueError("Importe inválido. Ejemplo: 0.10 o 2.50.") from None


def rank5(cards):
    """Tupla comparable: categoría seguida de todos los desempates necesarios."""
    values = sorted((RANKS.index(c[0]) + 2 for c in cards), reverse=True)
    counts = Counter(values)
    groups = sorted(((n, v) for v, n in counts.items()), reverse=True)
    unique = sorted(counts, reverse=True)
    straight = 0
    if len(unique) == 5:
        if unique[0] - unique[-1] == 4:
            straight = unique[0]
        elif unique == [14, 5, 4, 3, 2]:
            straight = 5
    flush = len({c[1] for c in cards}) == 1
    if flush and straight:
        return (8, straight)
    if groups[0][0] == 4:
        return (7, groups[0][1], groups[1][1])
    if [g[0] for g in groups] == [3, 2]:
        return (6, groups[0][1], groups[1][1])
    if flush:
        return (5, *values)
    if straight:
        return (4, straight)
    if groups[0][0] == 3:
        return (3, groups[0][1], *sorted((v for v in values if v != groups[0][1]), reverse=True))
    pairs = sorted((v for v, n in counts.items() if n == 2), reverse=True)
    if len(pairs) == 2:
        return (2, *pairs, next(v for v, n in counts.items() if n == 1))
    if pairs:
        return (1, pairs[0], *sorted((v for v in values if v != pairs[0]), reverse=True))
    return (0, *values)


def best_hand(cards):
    return max(rank5(hand) for hand in itertools.combinations(cards, 5))


def winning_hand(cards):
    chosen = max(itertools.combinations(cards, 5), key=rank5)
    return HAND_NAMES[rank5(chosen)[0]], list(chosen)


def cards_text(cards):
    return "  ".join(c[0].replace("T", "10") + SYMBOLS[c[1]] for c in cards)


@dataclass
class Player:
    pid: int
    name: str
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    stack: int = STACK
    connected: bool = True
    cards: list = field(default_factory=list)
    folded: bool = False
    bet: int = 0
    total: int = 0
    wagered: int = 0
    gained: int = 0
    lost: int = 0
    hands: int = 0
    wins: int = 0


class Game:
    """Motor independiente de interfaz y red; solo lo modifica el hilo servidor."""

    def __init__(self, starting_stack=STACK):
        if type(starting_stack) is not int or not BB <= starting_stack <= 100000000000:
            raise ValueError("El saldo inicial debe estar entre S/ 0.20 y S/ 1,000,000,000.00.")
        self.starting_stack = starting_stack
        self.players = []
        self.hand = []
        self.button = -1
        self.small = self.big = None
        self.board = []
        self.deck = []
        self.phase = "Sala"
        self.active = False
        self.showdown = False
        self.current = 0
        self.min_raise = BB
        self.pending = set()
        self.acted_at = {}
        self.turn = None
        self.deadline = 0
        self.number = 0
        self.revision = 0
        self.logs = deque(maxlen=100)
        self.results = []
        self.report = []
        self.report_hand = 0
        self.last_pot = 0
        self.match = 1
        self.event_id = 0
        self.events = deque(maxlen=24)
        self.ended = False
        self.end_reason = ""
        self.chat = deque(maxlen=100)
        self.chat_id = 0

    def chat_message(self, pid, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 300:
            raise ValueError("Escribe un mensaje de entre 1 y 300 caracteres.")
        text = " ".join(text.split())
        text = "".join(c for c in text if c.isprintable())
        if not text:
            raise ValueError("El mensaje está vacío.")
        self.chat_id += 1
        self.chat.append({"id": self.chat_id, "name": self.player(pid).name,
                          "pid": pid, "text": text, "time": time.strftime("%H:%M")})

    def end_disconnected(self, pid):
        if self.ended:
            return
        self.ended = True
        self.end_reason = f"{self.player(pid).name} se desconectó. La partida terminó."
        if self.active:
            for p in self.players:
                p.stack += p.total
                p.bet = p.total = 0
            self.report, self.results = [], []
            self.last_pot, self.report_hand = 0, 0
            self.end_reason += " Las apuestas de la mano interrumpida fueron devueltas."
        self.active, self.showdown = False, False
        self.phase, self.turn, self.deadline = "Partida terminada", None, 0
        self.current = 0
        self.pending.clear()
        self.log(self.end_reason)
        self.announce("ended", "PARTIDA TERMINADA", self.end_reason + "\nEl anfitrión puede iniciar una nueva partida cuando haya al menos dos jugadores conectados.")
        self.revision += 1

    def announce(self, kind, title, detail):
        self.event_id += 1
        self.events.append({"id": self.event_id, "match": self.match, "hand": self.number,
                            "kind": kind, "title": title, "detail": detail})

    def champion(self):
        if self.ended:
            return None
        remaining = [p for p in self.players if p.stack > 0]
        return remaining[0].pid if self.number and not self.active and len(remaining) == 1 else None

    def reset(self):
        """Nueva partida con los mismos asientos, conexiones e identidades."""
        if self.active:
            raise ValueError("Espera a que termine la mano para reiniciar.")
        for p in self.players:
            p.stack, p.bet, p.total, p.wagered = self.starting_stack, 0, 0, 0
            p.cards, p.folded = [], False
            p.gained = p.lost = p.hands = p.wins = 0
        self.hand, self.board, self.deck = [], [], []
        self.button, self.small, self.big = -1, None, None
        self.current, self.min_raise = 0, BB
        self.turn, self.deadline = None, 0
        self.pending, self.acted_at = set(), {}
        self.number, self.last_pot = 0, 0
        self.showdown, self.phase = False, "Sala"
        self.results, self.report = [], []
        self.report_hand = 0
        self.match += 1
        self.logs.clear()
        self.events.clear()
        self.ended, self.end_reason = False, ""
        self.log(f"Partida {self.match}: saldo inicial {money(self.starting_stack)} por jugador. Listos para repartir.")
        self.revision += 1

    def log(self, text):
        self.logs.append(text)

    def player(self, pid):
        return self.players[pid]

    def add(self, name, reuse=False):
        vacancy = next((p.pid for p in self.players if not p.connected), None) if reuse and not self.active else None
        if len(self.players) >= MAX_PLAYERS and vacancy is None:
            raise ValueError("Mesa llena (máximo 10 asientos por sesión).")
        name = " ".join(str(name).split())[:24]
        if not name:
            raise ValueError("Escribe un nombre.")
        if any(p.pid != vacancy and p.name.casefold() == name.casefold() for p in self.players):
            raise ValueError("Ese nombre ya está en uso; elige otro.")
        p = Player(len(self.players) if vacancy is None else vacancy, name, stack=self.starting_stack)
        if vacancy is None:
            self.players.append(p)
        else:
            self.players[vacancy] = p
        self.log(f"{name} se une a la mesa.")
        self.revision += 1
        return p

    def around(self, after, ids):
        return sorted(ids, key=lambda pid: (pid - after - 1) % len(self.players))

    def live(self):
        return [pid for pid in self.hand if not self.player(pid).folded]

    def actors(self):
        return [pid for pid in self.live() if self.player(pid).stack > 0]

    def pay(self, pid, amount):
        p = self.player(pid)
        amount = min(amount, p.stack)
        p.stack -= amount
        p.bet += amount
        p.total += amount
        p.wagered += amount

    def start(self):
        if self.ended:
            raise ValueError("La partida terminó por desconexión. Pulsa Nueva partida para volver a jugar.")
        if self.active:
            raise ValueError("Todavía hay una mano en curso.")
        ids = [p.pid for p in self.players if p.connected and p.stack > 0]
        if len(ids) < 2:
            raise ValueError("Se necesitan al menos 2 jugadores conectados con fichas.")
        self.hand = ids
        self.button = self.around(self.button, ids)[0]
        self.small = self.button if len(ids) == 2 else self.around(self.button, ids)[0]
        self.big = self.around(self.small, ids)[0]
        self.board = []
        self.deck = [r + s for r in RANKS for s in SUITS]
        secrets.SystemRandom().shuffle(self.deck)
        for p in self.players:
            p.cards, p.bet, p.total, p.folded = [], 0, 0, False
        for _ in range(2):
            for pid in self.around(self.button, ids):
                self.player(pid).cards.append(self.deck.pop())
        self.pay(self.small, SB)
        self.pay(self.big, BB)
        self.active, self.showdown = True, False
        self.phase = "Preflop"
        self.number += 1
        self.results = []
        self.current, self.min_raise = BB, BB
        self.pending, self.acted_at = set(self.actors()), {}
        self.log(f"— Mano {self.number}. Botón: {self.player(self.button).name}. Ciegas {money(SB)}/{money(BB)}. —")
        self.progress(self.big)
        self.revision += 1

    def raise_allowed(self, pid):
        return (len(self.actors()) > 1 and
                (pid not in self.acted_at or self.current - self.acted_at[pid] >= self.min_raise))

    def minimum_target(self):
        return self.current + self.min_raise

    def options(self, pid):
        if not self.active or self.turn != pid:
            return {}
        p = self.player(pid)
        maximum = p.bet + p.stack
        can_raise = self.raise_allowed(pid) and maximum > self.current
        return {"call": min(p.stack, max(0, self.current - p.bet)),
                "min": self.minimum_target(), "max": maximum,
                "raise": can_raise,
                "allin": maximum <= self.current or can_raise}

    def action(self, pid, kind, target=None):
        if not self.active or pid != self.turn:
            raise ValueError("No es tu turno.")
        p = self.player(pid)
        if kind == "allin":
            target = p.bet + p.stack
            kind = "call" if target <= self.current else "raise"
        if kind == "fold":
            p.folded = True
            self.log(f"{p.name} se retira.")
            self.announce("fold", f"{p.name} se retira", f"Apostó {money(p.total)} en esta mano.\nSaldo restante: {money(p.stack)}")
        elif kind == "call":
            amount = min(p.stack, max(0, self.current - p.bet))
            self.pay(pid, amount)
            self.acted_at[pid] = self.current
            self.log(f"{p.name}: {'pasa' if amount == 0 else 'iguala con ' + money(amount)}"
                     + (" (all-in)." if p.stack == 0 else "."))
        elif kind == "raise":
            if type(target) is not int:
                raise ValueError("La apuesta debe ser un número entero.")
            maximum = p.bet + p.stack
            if not self.raise_allowed(pid):
                raise ValueError("La acción no permite resubir; puedes igualar o retirarte.")
            if target <= self.current or target > maximum:
                raise ValueError("El total debe superar la apuesta actual y no exceder tus fichas.")
            if target < self.minimum_target() and target != maximum:
                raise ValueError(f"Subida mínima a {money(self.minimum_target())}, salvo all-in.")
            old = self.current
            if target >= self.minimum_target():
                self.min_raise = target - old
            self.pay(pid, target - p.bet)
            self.current = target
            self.acted_at[pid] = target
            self.pending.update(i for i in self.actors() if self.player(i).bet < target)
            self.log(f"{p.name} apuesta/sube a {money(target)}" + (" (all-in)." if p.stack == 0 else "."))
            self.announce("raise", f"{p.name} {'apuesta' if old == 0 else 'sube'} a {money(target)}",
                          f"Aumento de la apuesta: {money(target - old)}\nTotal en esta ronda: {money(target)} · Saldo: {money(p.stack)}"
                          + ("\nALL-IN" if p.stack == 0 else ""))
        else:
            raise ValueError("Acción desconocida.")
        self.pending.discard(pid)
        self.progress(pid)
        self.revision += 1

    def progress(self, after):
        """Avanza sin recursión: turnos, calles y runout cuando solo quedan all-ins."""
        while self.active:
            if len(self.live()) == 1:
                self.finish(False)
                return
            actors = self.actors()
            self.pending.intersection_update(actors)
            if len(actors) == 1:
                pid = actors[0]
                other_bet = max(self.player(i).bet for i in self.live() if i != pid)
                if self.player(pid).bet < other_bet:
                    self.current = other_bet
                    self.pending = {pid}
                else:
                    self.pending.clear()
            if self.pending:
                self.turn = self.around(after, self.pending)[0]
                self.deadline = time.monotonic() + TURN_SECONDS
                return
            if self.phase == "River":
                self.finish(True)
                return
            self.deck.pop()  # Carta quemada antes de cada calle.
            if self.phase == "Preflop":
                self.board.extend(self.deck.pop() for _ in range(3))
                self.phase = "Flop"
            elif self.phase == "Flop":
                self.board.append(self.deck.pop())
                self.phase = "Turn"
            else:
                self.board.append(self.deck.pop())
                self.phase = "River"
            for p in self.players:
                p.bet = 0
            self.current, self.min_raise = 0, BB
            self.pending, self.acted_at = set(self.actors()), {}
            after = self.button
            self.log(f"{self.phase}: {' '.join(self.board)}")

    def finish(self, showdown):
        self.showdown = showdown
        live = self.live()
        pot = sum(p.total for p in self.players)
        self.last_pot = pot
        self.report_hand = self.number
        awards = {pid: 0 for pid in self.hand}
        refunds = {pid: 0 for pid in self.hand}
        if not showdown:
            winner = self.player(live[0])
            other = max((p.total for p in self.players if p.pid != winner.pid), default=0)
            refunds[winner.pid] = max(0, winner.total - other)
            awards[winner.pid] = pot - refunds[winner.pid]
            winner.stack += pot
            self.results = [f"{winner.name} gana {money(awards[winner.pid])}: los demás se retiraron."]
            if refunds[winner.pid]:
                self.results.append(f"Se devuelven {money(refunds[winner.pid])} no igualadas a {winner.name}.")
        else:
            ranks = {pid: best_hand(self.player(pid).cards + self.board) for pid in live}
            levels = sorted({p.total for p in self.players if p.total})
            previous, pot_index = 0, 0
            self.results = []
            for level in levels:
                contributors = [p.pid for p in self.players if p.total >= level]
                amount = (level - previous) * len(contributors)
                previous = level
                if len(contributors) == 1:
                    p = self.player(contributors[0])
                    p.stack += amount
                    refunds[p.pid] += amount
                    self.results.append(f"Se devuelven {money(amount)} no igualadas a {p.name}.")
                    continue
                eligible = [pid for pid in contributors if pid in ranks]
                # En una mano válida, todo bote disputado tiene al menos un jugador vivo.
                if not eligible:
                    raise RuntimeError("Bote sin participante elegible.")
                best = max(ranks[pid] for pid in eligible)
                winners = self.around(self.button, [pid for pid in eligible if ranks[pid] == best])
                share, odd = divmod(amount, len(winners))
                label = "Bote principal" if pot_index == 0 else f"Bote secundario {pot_index}"
                payouts = []
                for index, pid in enumerate(winners):
                    won = share + (index < odd)
                    self.player(pid).stack += won
                    awards[pid] += won
                    payouts.append(f"{self.player(pid).name}: {money(won)}")
                self.results.append(f"{label} ({money(amount)}) — {', '.join(payouts)}; {HAND_NAMES[best[0]]}.")
                pot_index += 1
        self.report = [{"id": pid, "name": self.player(pid).name,
                        "wagered": self.player(pid).total, "won": awards[pid],
                        "refund": refunds[pid],
                        "net": awards[pid] + refunds[pid] - self.player(pid).total,
                        "stack": self.player(pid).stack,
                        "session_wagered": self.player(pid).wagered}
                       for pid in self.hand]
        for row in self.report:
            p = self.player(row["id"])
            p.gained += max(0, row["net"])
            p.lost += max(0, -row["net"])
            p.hands += 1
            p.wins += int(row["won"] > 0)
            if showdown and row["won"] > 0:
                row["hand_name"], row["best_five"] = winning_hand(p.cards + self.board)
            else:
                row["hand_name"], row["best_five"] = ("Victoria por retirada; sin showdown" if row["won"] else ""), []
        for line in self.results:
            self.log(line)
        for p in self.players:
            p.bet = p.total = 0
        self.active = False
        self.phase = "Showdown" if showdown else "Fin de mano"
        self.turn = None
        self.pending.clear()

        champion = self.champion()
        winners = [r for r in self.report if r["won"] > 0]
        title = (f"{self.player(champion).name} gana la partida" if champion is not None else
                 " / ".join(r["name"] for r in winners) + (" gana" if len(winners) == 1 else " ganan"))
        detail = "\n\n".join(f"{r['name']} · {r['hand_name'].upper()}\n{cards_text(r['best_five'])}\nGanó {money(r['won'])} · Apostó {money(r['wagered'])} · Neto {money(r['net'], True)}" for r in winners)
        self.announce("winner", title, detail)

    def tick(self):
        if self.active and time.monotonic() >= self.deadline:
            pid = self.turn
            self.log(f"Se agotó el tiempo de {self.player(pid).name}.")
            self.action(pid, "call" if self.options(pid)["call"] == 0 else "fold")
            return True
        return False

    def snapshot(self, pid, host=False):
        rows = []
        for p in self.players:
            visible = p.pid == pid or (self.showdown and p.pid in self.hand and not p.folded)
            rows.append({"id": p.pid, "name": p.name, "stack": p.stack,
                         "connected": p.connected, "bet": p.bet, "total": p.total,
                         "wagered": p.wagered,
                         "gained": p.gained, "lost": p.lost, "hands": p.hands, "wins": p.wins,
                         "in_hand": p.pid in self.hand, "folded": p.folded,
                         "cards": p.cards[:] if visible else ["??"] * len(p.cards)})
        return {"type": "state", "players": rows, "you": pid, "host": host,
                "phase": self.phase, "active": self.active, "hand": self.number,
                "button": self.button, "small": self.small, "big": self.big,
                "board": self.board[:], "pot": sum(p.total for p in self.players),
                "turn": self.turn, "seconds": max(0, self.deadline - time.monotonic()) if self.active else 0,
                "revision": self.revision, "current": self.current,
                "options": self.options(pid), "logs": list(self.logs), "results": self.results[:],
                "report": [row.copy() for row in self.report], "last_pot": self.last_pot,
                "report_hand": self.report_hand,
                "match": self.match, "champion": self.champion(), "starting_stack": self.starting_stack,
                "events": list(self.events), "event_id": self.event_id,
                "ended": self.ended, "end_reason": self.end_reason, "chat": list(self.chat)}


def encode(packet):
    return (json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class Peer:
    def __init__(self, sock):
        self.sock = sock
        self.incoming = bytearray()
        self.outgoing = bytearray()
        self.pid = None
        self.host = False
        self.created = time.monotonic()
        self.window, self.messages = self.created, 0
        self.last_seen, self.last_chat = self.created, 0


class Server:
    """Sockets no bloqueantes y un único hilo para todas las decisiones del juego."""
    def __init__(self, port=5050, pin="", host_key=None, starting_stack=STACK, online=False):
        self.game = Game(starting_stack)
        self.online = online
        self.pin = pin
        self.host_key = host_key
        self.host_pid = None
        self.sel = selectors.DefaultSelector()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind(("127.0.0.1" if online else "0.0.0.0", port))
            self.listener.listen(32)
            self.listener.setblocking(False)
            self.sel.register(self.listener, selectors.EVENT_READ, None)
        except Exception:
            self.listener.close()
            self.sel.close()
            raise
        self.port = self.listener.getsockname()[1]
        self.peers = {}
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="poker-servidor")
        self.failure = None

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def send(self, peer, packet):
        if peer.sock not in self.peers:
            return
        peer.outgoing.extend(encode(packet))
        if len(peer.outgoing) > 256000:
            self.drop(peer)
            return
        self.sel.modify(peer.sock, selectors.EVENT_READ | selectors.EVENT_WRITE, peer)

    def broadcast(self):
        for peer in list(self.peers.values()):
            if peer.pid is not None:
                self.send(peer, self.game.snapshot(peer.pid, peer.host))

    def drop(self, peer):
        if peer.sock not in self.peers:
            return
        self.peers.pop(peer.sock)
        try:
            self.sel.unregister(peer.sock)
        except (KeyError, ValueError):
            pass
        peer.sock.close()
        if peer.pid is not None:
            p = self.game.player(peer.pid)
            p.connected = False
            self.game.end_disconnected(peer.pid)
            if self.online and peer.pid == self.host_pid:
                remaining = [other for other in self.peers.values() if other.pid is not None]
                self.host_pid = remaining[0].pid if remaining else None
                for other in remaining:
                    other.host = other.pid == self.host_pid
                if remaining:
                    self.game.log(f"{self.game.player(self.host_pid).name} es ahora el anfitrión. Puede iniciar una nueva partida.")
            self.game.revision += 1

    def handle(self, peer, msg):
        if not isinstance(msg, dict):
            raise ValueError("Mensaje inválido.")
        if peer.pid is None:
            if msg.get("type") != "hello":
                raise ValueError("Primero debes identificarte.")
            if msg.get("protocol") != 4:
                raise ValueError("Todos deben usar poker_lan.py v4. Actualiza el archivo y vuelve a abrirlo.")
            if not isinstance(msg.get("pin"), str) or not secrets.compare_digest(msg["pin"].encode(), self.pin.encode()):
                raise ValueError("Clave de mesa incorrecta.")
            token = msg.get("token")
            if token:
                p = next((p for p in self.game.players if p.token == token), None)
                if p is None:
                    raise ValueError("La sesión anterior ya no existe. Cierra y abre el cliente.")
                if p.connected:
                    raise ValueError("Tu asiento aún está conectado. Espera unos segundos.")
                p.connected = True
                if self.online and self.host_pid is None:
                    self.host_pid = p.pid
                self.game.log(f"{p.name} volvió a conectarse.")
                self.game.revision += 1
            else:
                is_owner = bool(self.host_key and msg.get("host_key") == self.host_key)
                if self.host_key and self.host_pid is None and not is_owner:
                    raise ValueError("El anfitrión todavía está abriendo la mesa; reintenta.")
                p = self.game.add(msg.get("name", ""), reuse=self.online)
                if self.host_pid is None and (is_owner or self.host_key is None):
                    self.host_pid = p.pid
            peer.pid = p.pid
            peer.host = p.pid == self.host_pid
            self.send(peer, {"type": "welcome", "token": p.token, "id": p.pid, "protocol": 4})
            self.broadcast()
            return
        kind = msg.get("type")
        if kind == "ping":
            self.send(peer, {"type": "pong"})
            return
        elif kind == "chat":
            if time.monotonic() - peer.last_chat < 0.7:
                raise ValueError("Espera un momento antes de enviar otro mensaje.")
            self.game.chat_message(peer.pid, msg.get("text"))
            peer.last_chat = time.monotonic()
        elif kind == "reset":
            if not peer.host:
                raise ValueError("Solo el anfitrión puede reiniciar la partida.")
            if msg.get("revision") != self.game.revision:
                raise ValueError("La mesa cambió. Revisa el estado antes de reiniciar.")
            self.game.reset()
        elif kind == "start":
            if not peer.host:
                raise ValueError("Solo el anfitrión puede repartir.")
            self.game.start()
        elif kind == "action":
            if msg.get("revision") != self.game.revision:
                raise ValueError("La mesa cambió. Revisa el estado actualizado y repite la acción.")
            self.game.action(peer.pid, msg.get("action"), msg.get("amount"))
        else:
            raise ValueError("Mensaje desconocido.")
        self.broadcast()

    def run(self):
        try:
            while not self.stop_event.is_set():
                rev = self.game.revision
                for key, mask in self.sel.select(timeout=0.1):
                    if key.data is None:
                        sock, _ = self.listener.accept()
                        if len(self.peers) >= 32:
                            sock.close()
                            continue
                        sock.setblocking(False)
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        peer = Peer(sock)
                        self.peers[sock] = peer
                        self.sel.register(sock, selectors.EVENT_READ, peer)
                        continue
                    peer = key.data
                    if peer.sock not in self.peers:
                        continue
                    try:
                        if mask & selectors.EVENT_READ:
                            data = peer.sock.recv(4096)
                            if not data:
                                self.drop(peer)
                                continue
                            peer.incoming.extend(data)
                            peer.last_seen = time.monotonic()
                            if len(peer.incoming) > 16384:
                                self.drop(peer)
                                continue
                            while b"\n" in peer.incoming:
                                line, _, tail = peer.incoming.partition(b"\n")
                                peer.incoming = bytearray(tail)
                                now = time.monotonic()
                                if now - peer.window >= 1:
                                    peer.window, peer.messages = now, 0
                                peer.messages += 1
                                if peer.messages > 30 or len(line) > 4096:
                                    self.drop(peer)
                                    break
                                try:
                                    self.handle(peer, json.loads(line))
                                except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
                                    self.send(peer, {"type": "error", "message": str(exc)})
                                    if peer.pid is not None:
                                        self.send(peer, self.game.snapshot(peer.pid, peer.host))
                        if mask & selectors.EVENT_WRITE and peer.sock in self.peers:
                            sent = peer.sock.send(peer.outgoing)
                            del peer.outgoing[:sent]
                            if not peer.outgoing:
                                self.sel.modify(peer.sock, selectors.EVENT_READ, peer)
                    except BlockingIOError:
                        pass
                    except (OSError, ConnectionError):
                        self.drop(peer)
                for peer in list(self.peers.values()):
                    if peer.pid is None and time.monotonic() - peer.created > 10:
                        self.drop(peer)
                    elif peer.pid is not None and time.monotonic() - peer.last_seen > 12:
                        self.drop(peer)
                self.game.tick()
                if self.game.revision != rev:
                    self.broadcast()
        except Exception as exc:
            self.failure = str(exc)
            print(f"Error del servidor: {exc}", file=sys.stderr)
        finally:
            for peer in list(self.peers.values()):
                self.drop(peer)
            self.sel.close()
            self.listener.close()


class Client:
    """La red nunca accede a Tkinter: intercambia mensajes mediante colas."""
    def __init__(self, address, port, hello):
        self.address, self.port, self.hello = address, port, dict(hello, protocol=4)
        self.events = queue.Queue()
        self.commands = queue.Queue(maxsize=20)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="poker-cliente")
        self.thread.start()

    def send(self, message):
        try:
            self.commands.put_nowait(message)
        except queue.Full:
            self.events.put({"type": "error", "message": "Espera a que responda el servidor."})

    def close(self):
        self.stop_event.set()

    def run(self):
        sock = None
        try:
            sock = socket.create_connection((self.address, self.port), timeout=5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(0.2)
            sock.sendall(encode(self.hello))
            buffer = bytearray()
            last_ping = last_received = time.monotonic()
            while not self.stop_event.is_set():
                now = time.monotonic()
                if now - last_received > 12:
                    raise ConnectionError("Se perdió la comunicación con el anfitrión; la partida terminó.")
                if now - last_ping > 2:
                    sock.sendall(encode({"type": "ping"}))
                    last_ping = now
                while not self.commands.empty():
                    sock.sendall(encode(self.commands.get_nowait()))
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    raise ConnectionError("El servidor cerró la conexión.")
                last_received = time.monotonic()
                buffer.extend(data)
                if len(buffer) > 1000000:
                    raise ConnectionError("Respuesta demasiado grande.")
                while b"\n" in buffer:
                    line, _, tail = buffer.partition(b"\n")
                    buffer = bytearray(tail)
                    packet = json.loads(line)
                    if not isinstance(packet, dict):
                        raise ConnectionError("Respuesta inválida del servidor.")
                    if packet.get("type") == "welcome" and packet.get("protocol") != 4:
                        raise ConnectionError("El anfitrión debe abrir poker_lan.py v4. Actualicen todos el archivo.")
                    if packet.get("type") == "pong":
                        continue
                    self.events.put(packet)
        except (OSError, ValueError) as exc:
            if not self.stop_event.is_set():
                self.events.put({"type": "offline", "message": str(exc)})
        finally:
            if sock:
                sock.close()


class WebSession:
    """Un navegador tiene un cliente TCP propio: conserva las reglas y privacidad existentes."""
    def __init__(self, port, name, pin, token=None):
        self.client = Client("127.0.0.1", port, {"type": "hello", "name": name, "pin": pin, "token": token})
        self.name, self.token = name, token
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.state = None
        self.received_at = self.last_seen = time.monotonic()
        self.errors = deque(maxlen=10)
        self.online = True
        self.expired = False
        self.thread = threading.Thread(target=self.collect, daemon=True)
        self.thread.start()

    def collect(self):
        while not self.client.stop_event.is_set():
            try:
                msg = self.client.events.get(timeout=0.3)
            except queue.Empty:
                continue
            with self.lock:
                if msg["type"] == "welcome":
                    self.token = msg["token"]
                elif msg["type"] == "state":
                    self.state, self.received_at = msg, time.monotonic()
                    self.ready.set()
                elif msg["type"] in ("error", "offline"):
                    self.errors.append(msg["message"])
                    if msg["type"] == "offline" or self.state is None:
                        self.online = False
                        self.ready.set()

    def payload(self):
        with self.lock:
            self.last_seen = time.monotonic()
            state = dict(self.state) if self.state else None
            if state:
                state["seconds"] = max(0, state["seconds"] - (time.monotonic() - self.received_at))
            result = {"state": state, "online": self.online, "errors": list(self.errors)}
            self.errors.clear()
            return result

    def close(self):
        self.online = False
        self.client.close()


class WebHub:
    """HTTP local, sin paquetes adicionales. Las páginas no acceden al motor directamente."""
    def __init__(self, poker_server, port=5051, public_url=""):
        self.poker_server = poker_server
        self.public_url = public_url.rstrip("/")
        self.sessions = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(8)

            def reply(self, status, data, cookie=None, html=False):
                raw = data.encode("utf-8") if html else json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                if cookie:
                    secure = "; Secure" if hub.public_url.startswith("https://") else ""
                    self.send_header("Set-Cookie", f"poker_session={cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400{secure}")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def session(self):
                jar = SimpleCookie()
                try:
                    jar.load(self.headers.get("Cookie", ""))
                    sid = jar["poker_session"].value if "poker_session" in jar else ""
                except Exception:
                    sid = ""
                with hub.lock:
                    return sid, hub.sessions.get(sid)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/":
                    page = MOBILE_HTML
                    if hub.poker_server.online:
                        page = page.replace("Misma red Wi-Fi", "Wi-Fi o datos móviles").replace("Red local", "Mesa online")
                    self.reply(200, page, html=True)
                elif path == "/healthz":
                    healthy = hub.poker_server.thread.is_alive() and not hub.poker_server.failure
                    self.reply(200 if healthy else 503, {"ok": healthy})
                elif path == "/api/state":
                    _, session = self.session()
                    if session is None or session.expired:
                        self.reply(401, {"error": "Entra a la mesa para jugar."})
                    else:
                        self.reply(200, session.payload())
                elif path == "/api/info":
                    self.reply(200, {"version": 5, "starting_stack": hub.poker_server.game.starting_stack})
                else:
                    self.reply(404, {"error": "No encontrado."})

            def do_POST(self):
                # JSON + cabecera propia impiden envíos de formularios desde otros sitios.
                if self.headers.get("X-Poker") != "1" or self.headers.get_content_type() != "application/json":
                    self.reply(403, {"error": "Solicitud no permitida."})
                    return
                origin = self.headers.get("Origin")
                if origin and (origin != hub.public_url if hub.public_url else urlsplit(origin).netloc != self.headers.get("Host")):
                    self.reply(403, {"error": "Origen no permitido."})
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        raise ValueError("Solicitud demasiado grande o vacía.")
                    msg = json.loads(self.rfile.read(size))
                    if not isinstance(msg, dict):
                        raise ValueError("Solicitud inválida.")
                    path = urlsplit(self.path).path
                    sid, session = self.session()
                    if path == "/api/join":
                        if session and session.online and not session.expired:
                            self.reply(200, session.payload())
                            return
                        name, pin = msg.get("name", ""), msg.get("pin", "")
                        if not isinstance(name, str) or not isinstance(pin, str) or len(name) > 24 or len(pin) > 128:
                            raise ValueError("Revisa tu nombre y la clave.")
                        with hub.lock:
                            if hub.poker_server.online and len(hub.sessions) >= 32 and not session:
                                oldest = next((key for key, value in hub.sessions.items() if value.expired), None)
                                if oldest:
                                    del hub.sessions[oldest]
                            if len(hub.sessions) >= 32 and not session:
                                raise ValueError("El servidor tiene demasiadas sesiones.")
                        candidate = WebSession(hub.poker_server.port, name, pin, session.token if session else None)
                        if not candidate.ready.wait(6) or not candidate.online:
                            error = candidate.payload()["errors"]
                            candidate.close()
                            if session and session.token and error and "sesión anterior" in error[-1]:
                                with hub.lock:
                                    hub.sessions.pop(sid, None)
                                raise ValueError("Tu asiento anterior fue liberado. Pulsa Entrar otra vez para ocupar uno nuevo.")
                            raise ValueError(error[-1] if error else "No se pudo conectar. Reintenta.")
                        sid = sid or secrets.token_urlsafe(32)
                        with hub.lock:
                            hub.sessions[sid] = candidate
                        self.reply(200, candidate.payload(), cookie=sid)
                        return
                    if not session or not session.online or session.expired:
                        self.reply(401, {"error": "Tu conexión terminó. Vuelve a entrar."})
                        return
                    session.last_seen = time.monotonic()
                    if path == "/api/leave":
                        session.expired = True
                        session.close()
                        self.reply(200, {"ok": True})
                    elif path == "/api/command":
                        if msg.get("type") not in ("action", "chat", "start", "reset"):
                            raise ValueError("Acción no permitida.")
                        session.client.send(msg)
                        self.reply(200, {"ok": True})
                    else:
                        self.reply(404, {"error": "No encontrado."})
                except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
                    self.reply(400, {"error": str(exc)})

        self.http = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self.http.daemon_threads = True
        self.port = self.http.server_port
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self.cleanup_thread = threading.Thread(target=self.cleanup, daemon=True)

    def start(self):
        self.thread.start()
        self.cleanup_thread.start()

    def cleanup(self):
        while not self.stop_event.wait(1):
            with self.lock:
                sessions = list(self.sessions.values())
            for session in sessions:
                if not session.expired and time.monotonic() - session.last_seen > 20:
                    session.expired = True
                    session.close()
            with self.lock:
                for sid, session in list(self.sessions.items()):
                    if session.expired and time.monotonic() - session.last_seen > 86400:
                        hub_session = self.sessions.get(sid)
                        if hub_session is session:
                            del self.sessions[sid]

    def stop(self):
        self.stop_event.set()
        self.http.shutdown()
        self.http.server_close()
        with self.lock:
            for session in self.sessions.values():
                session.close()
        self.thread.join(timeout=2)


MOBILE_HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#071d1b"><title>Círculo · Póker LAN</title>
<style>
:root{color-scheme:dark;--bg:#071310;--panel:#10241f;--gold:#edcb85;--text:#eff6f0;--muted:#9caf9f;--green:#a1ecc0;--line:#2b4234}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 45% 0,#18352b 0,#091712 48%,#050c09 100%);color:var(--text);font:15px system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100dvh}button,input{font:inherit}button{cursor:pointer;border:1px solid var(--line);border-radius:12px;padding:12px 18px;color:var(--text);background:#1c342b;font-weight:650;min-height:44px}button:hover:not(:disabled){filter:brightness(1.16)}button:disabled{opacity:.38;cursor:default}button.gold{background:linear-gradient(135deg,#f0d99d,#be9858);color:#20190d;border-color:#e9c88a}button.danger{color:#ffc0ae;background:#432721;border-color:#644037}button.quiet{background:transparent}input{border:1px solid #42604a;background:#0b1913;color:white;border-radius:10px;padding:12px;min-width:0;outline:none}input:focus-visible,button:focus-visible{outline:2px solid var(--gold);outline-offset:3px}a{color:var(--gold)}[hidden]{display:none!important}.muted{color:var(--muted)}.eyebrow{font-size:11px;letter-spacing:3px;color:var(--gold);text-transform:uppercase}h1,h2,h3,p{margin:0}header{max-width:1360px;margin:auto;padding:24px 32px 14px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #ffffff10}.brand{display:flex;gap:12px;align-items:center}.brand-mark{font:44px Georgia;color:var(--gold)}.brand h1{font:25px Georgia,serif;letter-spacing:5px}.brand p{font-size:10px;letter-spacing:2px;color:var(--muted);margin-top:4px}.connection{font-size:12px;color:var(--green);display:flex;gap:8px;align-items:center}.dot{background:#8af0ad;border-radius:50%;width:7px;height:7px;box-shadow:0 0 12px #78e8ab}.top-actions{display:flex;align-items:center;gap:14px}.leave{font-size:12px;min-height:34px;padding:7px 12px}.layout{max-width:1360px;margin:0 auto;padding:18px 24px 30px;display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:24px}.main{min-width:0}.table-head{display:flex;justify-content:space-between;align-items:center;padding:0 10px}.table-head h2{font:24px Georgia;letter-spacing:.3px}.table-head .stakes{text-align:right;font-size:12px;color:var(--muted);line-height:1.8}.table-head strong{color:var(--gold)}.stage{height:660px;position:relative;isolation:isolate;overflow:hidden}.felt{position:absolute;left:50%;top:51%;width:min(69%,530px);aspect-ratio:1;border-radius:50%;transform:translate(-50%,-50%);background:radial-gradient(ellipse at 40% 30%,#24694e,#104632 60%,#073320);border:15px solid #493822;box-shadow:0 0 0 2px #b2985c,0 0 0 8px #171c13,0 0 0 10px #72603e,0 28px 65px #000a,inset 0 0 40px #021b10}.felt:before{content:"";position:absolute;inset:11px;border:1px solid #debd6a60;border-radius:50%}.felt:after{content:"C Í R C U L O";position:absolute;top:25%;width:100%;text-align:center;color:#c3d7b51a;font:23px Georgia;letter-spacing:6px}.dealer{position:absolute;left:50%;top:0;transform:translateX(-50%);display:flex;align-items:center;flex-direction:column;z-index:3}.dealer svg{width:54px;height:64px;filter:drop-shadow(0 5px 8px #0009)}.dealer span{font-size:9px;color:var(--gold);letter-spacing:2px;margin-top:3px}.dealer.dealing svg{animation:dealer-bob .45s ease-in-out 4}@keyframes dealer-bob{50%{transform:translateY(3px) rotate(4deg)}}.deck{position:absolute;top:31%;left:50%;transform:translateX(-50%);width:24px;height:33px;border-radius:4px;background:repeating-linear-gradient(45deg,#cfb374 0 2px,#213b30 2px 5px);border:2px solid #e3d3a4;box-shadow:3px 3px 0 #cfcca7;z-index:2}.center{position:absolute;left:50%;top:48%;width:54%;transform:translate(-50%,-50%);z-index:3;text-align:center}.pot-caption{font-size:9px;letter-spacing:2.5px;color:#c2d0bc}.pot{font-size:26px;color:#ffe2a4;font-weight:750;margin:2px 0 14px;text-shadow:0 2px 5px #0008}.board{display:flex;justify-content:center;gap:5px}.card{display:inline-flex;flex-direction:column;justify-content:space-between;align-items:flex-start;background:linear-gradient(140deg,#fffdf0,#eae3ce);color:#172d22;border-radius:6px;width:48px;height:68px;padding:4px 7px;box-shadow:0 3px 4px #0005;font:700 22px Georgia;border:1px solid #fff9;position:relative}.card .suit{align-self:flex-end;font-size:24px}.card.red{color:#b73830}.card.back{color:#e8c983;background:repeating-linear-gradient(45deg,#b3935544 0 1px,#172f26 1px 6px);border:1px solid #d1b879;align-items:center;justify-content:center}.card.blank{background:#052b1b60;border:1px dashed #99be9870;box-shadow:none;color:#96b39355;justify-content:center;align-items:center}.phase{font-size:10px;letter-spacing:2px;color:#bfccb4;margin-top:13px;text-transform:uppercase}.seat{position:absolute;transform:translate(-50%,-50%);width:126px;text-align:center;z-index:5;transition:left .4s,top .4s}.seat-main{background:linear-gradient(150deg,#1e3429,#101d16);border:1px solid #5c6747;border-radius:13px;padding:8px 6px;box-shadow:0 6px 15px #0006}.seat.turn .seat-main{border:2px solid var(--gold);box-shadow:0 0 22px #ecc47a33;padding:7px 5px}.seat.folded{opacity:.55}.seat.me .seat-main{background:linear-gradient(130deg,#315b40,#162d21)}.avatar{width:25px;height:25px;border:1px solid #ffffff20;background:#46563b;border-radius:50%;margin:0 auto 4px;display:grid;place-items:center;color:var(--gold);font-size:11px;font-weight:bold}.seat-name{white-space:nowrap;text-overflow:ellipsis;overflow:hidden;font-size:12px;font-weight:650}.seat-money{font-size:12px;color:var(--gold);margin-top:3px;font-variant-numeric:tabular-nums}.seat-role{font-size:9px;color:#b2c9a9;margin-top:3px;min-height:12px}.seat-cards{display:flex;justify-content:center;gap:3px;margin-bottom:-2px}.seat-cards .card{width:26px;height:35px;font-size:12px;padding:2px 4px;border-radius:4px}.seat-cards .card .suit{font-size:14px}.seat-bet{font-size:10px;margin-top:5px;color:#cfddc1;min-height:15px}.seat-bet:before{content:"●";color:var(--gold);margin-right:4px}.button-disc{position:absolute;right:-9px;top:42%;background:#efe6c7;color:#152519;border:2px solid #ad9e70;border-radius:50%;width:21px;height:21px;display:grid;place-items:center;font-size:10px;font-weight:bold}.chip-stack{position:absolute;left:50%;top:60%;transform:translate(-50%,-50%);display:flex;gap:5px;z-index:3}.chip{width:23px;height:23px;border:3px dashed #ffecb4;border-radius:50%;background:#b45b31;box-shadow:0 2px 0 #5a2717,0 4px 3px #0006}.chip:nth-child(2){background:#367758}.chip:nth-child(3){background:#334e80}.fly-chip{position:absolute;width:24px;height:24px;border:4px dashed #ffe7ae;background:#bd783f;border-radius:50%;z-index:20;box-shadow:0 3px 7px #0007;pointer-events:none}.fly-card{position:absolute;width:26px;height:36px;z-index:20;background:repeating-linear-gradient(45deg,#d9ba7844 0 2px,#1a3528 2px 5px);border:1px solid #d4ba7c;border-radius:4px;pointer-events:none}.turn-strip{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;margin:0 4px 9px;color:var(--muted)}.turn-strip b{color:var(--gold)}.timer{height:3px;background:#20362a;border-radius:3px;margin-bottom:13px}.timer i{display:block;height:100%;background:var(--gold);width:0;transition:width .3s}.control-panel{border:1px solid #4e5b3b;border-radius:18px;background:linear-gradient(130deg,#1b3023,#101d16);padding:16px}.hand-wallet{display:flex;justify-content:space-between;align-items:center;margin-bottom:13px}.hand-wallet h3{font-size:13px;color:var(--muted);font-weight:500}.wallet{font-size:22px;color:var(--gold);font-weight:700}.my-cards{display:flex;gap:6px}.my-cards .card{width:37px;height:51px;font-size:18px}.my-cards .card .suit{font-size:19px}.action-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}.raise-line{display:flex;gap:8px;margin-top:10px}.raise-line label{display:flex;align-items:center;gap:6px;background:#091810;border:1px solid #3b5941;border-radius:11px;padding-left:12px;color:var(--gold);flex:1;min-width:0}.raise-line input{width:100%;border:0;background:transparent;padding-left:4px;font-size:17px}.raise-line button{flex:1}.quick{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}.quick button{font-size:11px;min-height:30px;padding:5px 10px;border-radius:8px}.hint{font-size:11px;color:var(--muted);line-height:1.6;margin-top:8px}.host-controls{display:flex;gap:8px;margin-top:14px}.host-controls button{flex:1}.side{border:1px solid #ffffff15;border-radius:18px;background:#0e1d16;overflow:hidden;align-self:start;position:sticky;top:16px}.tabs{display:flex;border-bottom:1px solid #ffffff12}.tabs button{border:0;border-radius:0;padding:14px 8px;flex:1;font-size:12px;background:transparent;color:var(--muted)}.tabs button.active{color:var(--gold);box-shadow:inset 0 -2px var(--gold)}.pane{padding:16px}.pane h3{font:20px Georgia;color:#e9d9b2;margin-bottom:14px}.chatlog{height:320px;overflow:auto;overflow-wrap:anywhere}.chat-message{margin-bottom:17px;font-size:13px;line-height:1.5}.chat-author{color:var(--gold);font-size:11px;display:flex;justify-content:space-between;margin-bottom:3px}.chat-compose{display:flex;gap:6px;border-top:1px solid #ffffff15;padding-top:12px}.chat-compose input{width:100%;font-size:13px;padding:10px}.chat-compose button{padding:8px 12px}.profile-row,.result-row{padding:12px 0;border-bottom:1px solid #ffffff10;font-size:12px;line-height:1.8}.profile-row strong,.result-row strong{color:var(--gold);font-size:14px}.profile-stats{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:8px}.profile-stats div{background:#193022;border-radius:8px;padding:7px}.profile-stats span{display:block;color:var(--muted);font-size:10px}.plus{color:var(--green)}.minus{color:#ffb0a0}.result-cards{display:flex;gap:4px;margin-top:8px}.result-cards .card{width:32px;height:46px;font-size:15px;padding:3px}.result-cards .card .suit{font-size:16px}.notes{padding:18px;font-size:11px;line-height:1.8;color:var(--muted);border-top:1px solid #ffffff10}.announcement{position:fixed;inset:0;background:#03130cc9;backdrop-filter:blur(7px);display:grid;place-items:center;z-index:100;padding:20px}.announcement-box{background:radial-gradient(ellipse at top,#2b4d35,#0f2218 65%);border:1px solid #cbb17a;border-radius:24px;padding:34px 24px;text-align:center;width:min(540px,100%);box-shadow:0 35px 100px #000b;max-height:85dvh;overflow:auto}.announcement-box h2{font:32px Georgia;color:#ffe8ac;margin:13px 0}.announcement-box p{white-space:pre-line;line-height:1.8;font-size:15px}.announcement-box button{margin-top:22px}.announcement-icon{font:48px Georgia;color:var(--gold)}.toast{position:fixed;left:50%;bottom:25px;transform:translateX(-50%);background:#493025;border:1px solid #b99762;color:#fff0df;padding:12px 20px;max-width:90%;border-radius:12px;z-index:200;font-size:13px;box-shadow:0 8px 25px #0009}.login{min-height:calc(100dvh - 100px);display:grid;place-items:center;padding:28px 20px}.login-box{max-width:430px;width:100%;background:linear-gradient(150deg,#203c2a,#0d1e14);border:1px solid #6b6742;border-radius:25px;padding:34px;box-shadow:0 30px 100px #0006}.login-box h2{font:38px Georgia;color:#efd99e;margin:12px 0}.login-box p{color:var(--muted);font-size:14px;line-height:1.7;margin-bottom:20px}.login-box label{font-size:12px;display:block;margin:16px 0 7px;color:#d8dec8}.login-box input{width:100%}.login-box button{width:100%;margin-top:22px}.login-art{height:95px;display:flex;justify-content:center;padding-top:10px}.login-art .card{width:54px;height:77px;font-size:25px}.login-art .card:first-child{transform:rotate(-14deg) translateX(8px)}.login-art .card:last-child{transform:rotate(12deg) translateX(-6px)}.login-foot{margin-top:17px;color:var(--muted);font-size:11px;text-align:center;line-height:1.7}
@media(max-width:1050px){.layout{grid-template-columns:minmax(0,1fr) 260px;gap:14px;padding:18px 16px}.stage{height:590px}.seat{width:108px}.pot{font-size:22px}.card{width:39px;height:57px;font-size:19px;padding:4px}.board{gap:4px}.side .pane{padding:12px}}
@media(max-width:800px){header{padding:16px 18px 12px}.brand h1{font-size:21px}.brand-mark{font-size:34px}.connection{font-size:10px}.layout{display:flex;flex-direction:column;padding:15px 12px 25px;gap:20px}.stage{height:560px}.felt{width:71%;max-width:420px;border-width:11px}.seat{width:108px}.side{position:static;width:100%}.chatlog{height:230px}.table-head h2{font-size:21px}.pot{font-size:23px}.card{width:43px;height:61px}.top-actions{gap:7px}.notes{font-size:11px}.control-panel{padding:14px}.announcement-box h2{font-size:28px}}
@media(max-width:440px){.stage{height:485px;margin-top:8px}.seat{width:96px}.seat-main{padding:6px 4px}.seat.turn .seat-main{padding:5px 3px}.seat-name{font-size:11px}.seat-money{font-size:10px}.avatar{width:20px;height:20px;font-size:9px}.seat-role{font-size:8px}.seat-cards .card{width:22px;height:30px;font-size:11px}.seat-cards .card .suit{font-size:12px}.center{width:56%;top:49%}.card{width:33px;height:48px;font-size:17px;border-radius:4px;padding:3px 4px}.card .suit{font-size:18px}.board{gap:3px}.pot{font-size:22px;margin-bottom:10px}.felt{width:73%;border-width:9px}.felt:after{font-size:13px;letter-spacing:3px;top:20%}.dealer svg{width:42px;height:49px}.dealer{top:0}.deck{top:30%;width:17px;height:25px}.phase{font-size:8px;margin-top:9px}.chip-stack{top:61%;gap:3px}.chip{width:18px;height:18px}.action-grid button{padding:11px 7px;font-size:13px}.raise-line button{font-size:13px}.brand p{font-size:8px}.table-head .stakes{font-size:10px}.table-head h2{font-size:20px}.seat-bet{font-size:9px}.announcement-box{padding:25px 18px}.announcement-box p{font-size:13px}.connection span.label{display:none}.login-box{padding:27px}}
.stage:has(.seat:nth-child(7)) .seat{width:100px}.stage:has(.seat:nth-child(7)) .avatar{display:none}.stage:has(.seat:nth-child(7)) .seat-main{padding:6px 4px}.stage:has(.seat:nth-child(7)) .seat-cards .card{width:23px;height:31px;font-size:12px}
@media(max-width:440px){.stage:has(.seat:nth-child(7)){height:570px}.stage:has(.seat:nth-child(7)) .seat{width:78px}.stage:has(.seat:nth-child(7)) .seat-name{font-size:10px}.stage:has(.seat:nth-child(7)) .seat-money{font-size:9px}.stage:has(.seat:nth-child(7)) .seat-role{font-size:7px}.stage:has(.seat:nth-child(7)) .seat-cards .card{width:20px;height:27px;font-size:11px}.stage:has(.seat:nth-child(7)) .seat-bet{font-size:8px}}
.chip-stack{top:66%!important}
.announcement.compact{inset:90px 14px auto;display:block;background:none;backdrop-filter:none;pointer-events:none;padding:0}.announcement.compact .announcement-box{max-width:440px;margin:auto;padding:15px 20px;border-radius:16px;box-shadow:0 12px 35px #0007}.announcement.compact .announcement-icon,.announcement.compact button{display:none}.announcement.compact h2{font:22px Georgia;margin:7px 0}.announcement.compact p{font-size:12px;line-height:1.5}.announcement.compact .eyebrow{font-size:9px}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}
</style></head><body>
<header><div class="brand"><div class="brand-mark">♠</div><div><h1>CÍRCULO</h1><p>TEXAS HOLD’EM · MESA PRIVADA</p></div></div><div class="top-actions"><div class="connection"><i class="dot" id="netDot"></i><span id="network">Red local</span></div><button class="leave quiet" id="leave" hidden>Salir</button></div></header>
<section class="login" id="login"><form class="login-box" id="joinForm"><div class="eyebrow">Tu lugar en la mesa</div><h2>Una buena mano.<br>Buena compañía.</h2><p>Entra con tu nombre. El crupier reparte y la mesa hace el resto.</p><div class="login-art"><div class="card">A<span class="suit">♠</span></div><div class="card red">K<span class="suit">♥</span></div></div><label for="name">Tu nombre</label><input id="name" maxlength="24" autocomplete="nickname" required placeholder="¿Cómo te llamas?"><label for="pin">Clave de la mesa</label><input id="pin" maxlength="128" type="password" autocomplete="off" placeholder="Déjala vacía si no hay clave"><button class="gold" id="joinBtn">Entrar a la mesa →</button><div class="login-foot">Saldo inicial <strong id="initialBalance">S/ 10,000.00</strong> virtuales<br>Misma red Wi-Fi · Sin instalar aplicaciones</div></form></section>
<div class="layout" id="game" hidden><main class="main"><div class="table-head"><div><div class="eyebrow" style="margin-bottom:7px">SALA PRIVADA · SIN LÍMITE</div><h2>Mesa de amigos</h2></div><div class="stakes">Ciegas <strong>S/ 0.10 / 0.20</strong><br><span id="handNo">Esperando jugadores</span></div></div>
<div class="stage" id="stage"><div class="felt"></div><div class="dealer" id="dealer" aria-label="Crupier virtual"><svg viewBox="0 0 64 80" role="img" aria-label="Crupier"><path fill="#17261f" stroke="#b5a16f" d="M4 79V64Q6 45 32 44Q59 45 60 64V79Z"/><path fill="#efead3" d="M22 46L32 65L43 46L38 80H26Z"/><path fill="#bd965e" d="M25 37h14v13L32 57l-7-7z"/><ellipse cx="32" cy="25" rx="15" ry="20" fill="#d9b483"/><path fill="#283023" d="M16 27V16Q19 0 34 3Q52 4 48 23L43 12Q28 22 19 17Z"/><path fill="#183126" d="M23 50l9 4-8 7zM41 50l-9 4 8 7z"/><circle cx="26" cy="26" r="1.5" fill="#383023"/><circle cx="38" cy="26" r="1.5" fill="#383023"/><path d="M27 35q5 4 10 0" fill="none" stroke="#876144" stroke-width="1.5"/><path fill="#cba96c" d="M7 73h12v3H7z"/></svg><span>CRUPIER</span></div><div class="deck" aria-hidden="true"></div><div class="center"><div class="pot-caption" id="potCaption">BOTE VIRTUAL</div><div class="pot" id="pot">S/ 0.00</div><div class="board" id="board"></div><div class="phase" id="phase">La mesa te espera</div></div><div class="chip-stack" id="potChips" hidden><i class="chip"></i><i class="chip"></i><i class="chip"></i></div><div id="seats"></div></div>
<div class="turn-strip"><span id="turnText">Esperando el reparto</span><span id="clock">—</span></div><div class="timer"><i id="timerBar"></i></div>
<section class="control-panel"><div class="hand-wallet"><div><h3>Tu saldo virtual</h3><div class="wallet" id="wallet">—</div></div><div class="my-cards" id="myCards"></div></div><div class="action-grid"><button class="danger" id="fold" disabled>Retirarme</button><button class="gold" id="call" disabled>Pasar</button><button id="allin" disabled>All-in</button></div><div class="raise-line"><label>S/ <input id="amount" inputmode="decimal" type="text" value="0.40" aria-label="Total de la apuesta en soles"></label><button class="gold" id="raise" disabled>Subir / apostar</button></div><div class="quick"><button data-quick="min">Mínima</button><button data-quick="half">½ bote</button><button data-quick="pot">Bote</button><button data-quick="100">S/ 100</button></div><div class="hint" id="hint">El importe es el total de tu apuesta en esta ronda.</div><div class="host-controls" id="hostControls" hidden><button class="gold" id="start">Repartir mano</button><button id="reset">Nueva partida</button></div></section></main>
<aside class="side"><nav class="tabs"><button class="active" data-pane="chat">Chat <span id="unread"></span></button><button data-pane="results">Resultados</button><button data-pane="profiles">Perfiles</button></nav><section class="pane" id="pane-chat"><h3>La conversación</h3><div class="chatlog" id="chatLog" aria-live="polite"></div><form class="chat-compose" id="chatForm"><input id="chatInput" maxlength="300" placeholder="Escribe a la mesa…" aria-label="Mensaje al chat"><button class="gold" aria-label="Enviar mensaje">↑</button></form></section><section class="pane" id="pane-results" hidden><h3>Última mano</h3><div id="results" style="max-height:420px;overflow:auto">El resultado aparecerá aquí.</div></section><section class="pane" id="pane-profiles" hidden><h3>Los jugadores</h3><div id="profiles" style="max-height:460px;overflow:auto"></div></section><div class="notes">♠ &nbsp;Dinero virtual. Solo entre amigos.<br>Conserva esta página abierta durante la partida. Si un jugador se desconecta, termina la partida y se devuelven las apuestas pendientes.</div></aside></div>
<div class="announcement" id="announcement" hidden role="dialog" aria-modal="true" aria-labelledby="announcementTitle"><div class="announcement-box"><div class="eyebrow" id="announcementBadge">EN LA MESA</div><div class="announcement-icon">♠</div><h2 id="announcementTitle"></h2><p id="announcementDetail"></p><button class="gold" id="dismiss">Volver a la mesa</button></div></div><div class="toast" id="toast" hidden role="status"></div>
<script>
'use strict';
const $=id=>document.getElementById(id), money=n=>'S/ '+(Math.abs(n)/100).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}), signed=n=>(n<0?'−':n>0?'+':'')+money(n);
let state=null, lastState=null, online=false, busy=false, polling=false, eventCursor=null, scope='', announcementQueue=[], announcementTimer=null, toastTimer=null, activePane='chat', chatID=0, stateAt=0, turnKey='', positions=new Map(), animScope='', pollFailures=0;
const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
function card(code){let e=node('span','card');if(!code){e.classList.add('blank');e.textContent='♠';return e}if(code==='??'){e.classList.add('back');e.textContent='♠';return e}if('dh'.includes(code[1]))e.classList.add('red');e.append(node('span','',code[0]==='T'?'10':code[0]),node('span','suit',({c:'♣',d:'♦',h:'♥',s:'♠'})[code[1]]));return e}
function showToast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5500)}
async function api(path,data){const options={credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout?AbortSignal.timeout(8000):undefined};if(data!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-Poker':'1'};options.body=JSON.stringify(data)}const r=await fetch(path,options);const body=await r.json();if(!r.ok){const err=new Error(body.error||'No se pudo completar la acción');err.status=r.status;throw err}return body}
function cents(text){text=text.trim().replace(',','.');if(!/^\d+(\.\d{1,2})?$/.test(text))throw new Error('Usa soles y hasta dos decimales. Ejemplo: 0.50');const [whole,fraction='']=text.split('.');const n=Number(whole)*100+Number(fraction.padEnd(2,'0'));if(!Number.isSafeInteger(n)||n>100000000000)throw new Error('Importe demasiado grande');return n}
function showLogin(){online=false;$('game').hidden=true;$('login').hidden=false;$('leave').hidden=true;$('network').textContent='Red local';state=null;eventCursor=null;scope='';animScope='';announcementQueue=[];dismiss(false)}
function accept(payload){online=payload.online;pollFailures=0;for(const error of payload.errors||[])showToast(error);if(payload.state){lastState=state;state=payload.state;stateAt=performance.now();$('login').hidden=true;$('game').hidden=false;$('leave').hidden=false;render();receiveEvents()}if(!online){$('network').textContent='Sin conexión';showToast('La conexión terminó. Vuelve a entrar para recuperar tu asiento.');controls()} }
$('joinForm').addEventListener('submit',async e=>{e.preventDefault();$('joinBtn').disabled=true;try{const p=await api('/api/join',{name:$('name').value.trim(),pin:$('pin').value});try{localStorage.setItem('pokerName',$('name').value.trim())}catch{}accept(p)}catch(err){showToast(err.message)}finally{$('joinBtn').disabled=false}});
async function poll(){if(polling)return;polling=true;try{accept(await api('/api/state'))}catch(err){if(err.status===401){if(state)showToast('La sesión terminó. Vuelve a entrar a la mesa.');showLogin()}else{pollFailures++;$('network').textContent='Reconectando…';if(pollFailures>=3){online=false;controls();showToast('No se puede contactar con la Mac. Revisa el Wi-Fi.')}}}finally{polling=false}}
async function command(cmd){if(!online||!state)return;busy=true;controls();try{await api('/api/command',cmd);await poll()}catch(err){showToast(err.message);if(err.status===401)showLogin()}finally{busy=false;controls()}}
function act(action){if(!state||!state.options||state.turn!==state.you)return;const msg={type:'action',action,revision:state.revision};try{if(action==='raise')msg.amount=cents($('amount').value);command(msg)}catch(err){showToast(err.message)}}
$('fold').onclick=()=>act('fold');$('call').onclick=()=>act('call');$('allin').onclick=()=>act('allin');$('raise').onclick=()=>act('raise');$('start').onclick=()=>command({type:'start'});$('reset').onclick=()=>{if(confirm('¿Empezar otra partida con '+money(state.starting_stack)+' virtuales por jugador?'))command({type:'reset',revision:state.revision})};
$('leave').onclick=async()=>{if(!confirm('Si sales, termina la partida para todos. ¿Salir?'))return;try{await api('/api/leave',{})}catch{}showLogin()};
$('chatForm').onsubmit=async e=>{e.preventDefault();const text=$('chatInput').value.trim();if(!text)return;try{await api('/api/command',{type:'chat',text});$('chatInput').value='';await poll()}catch(err){showToast(err.message)}};
document.querySelectorAll('[data-pane]').forEach(b=>b.onclick=()=>{activePane=b.dataset.pane;document.querySelectorAll('[data-pane]').forEach(x=>x.classList.toggle('active',x===b));for(const p of ['chat','results','profiles'])$('pane-'+p).hidden=p!==activePane;if(activePane==='chat')$('unread').textContent=''});
document.querySelectorAll('[data-quick]').forEach(b=>b.onclick=()=>{if(!state?.options?.raise)return;const o=state.options;let val=o.min;const q=b.dataset.quick;if(q==='half')val=state.current+Math.floor(state.pot/2);if(q==='pot')val=state.current+state.pot;if(q==='100')val=10000;val=Math.max(o.min,Math.min(o.max,val));$('amount').value=(Math.min(o.max,val)/100).toFixed(2)});
function controls(){const o=state?.options||{}, turn=online&&!busy&&state?.turn===state?.you&&state?.active;for(const id of ['fold','call'])$(id).disabled=!turn;$('allin').disabled=!turn||!o.allin;$('raise').disabled=!turn||!o.raise||o.max<o.min;$('amount').disabled=!turn||!o.raise;document.querySelectorAll('[data-quick]').forEach(b=>b.disabled=!turn||!o.raise);$('call').textContent=o.call?'Igualar '+money(o.call):'Pasar';$('hostControls').hidden=!state?.host;if(state){$('start').disabled=busy||!online||state.active||state.ended||state.players.filter(p=>p.connected&&p.stack>0).length<2;$('reset').disabled=busy||!online||state.active||(!state.hand&&!state.ended)||state.players.filter(p=>p.connected).length<2;$('hint').textContent=turn?'Mínimo total '+money(o.min)+' · Máximo '+money(o.max)+'. Incluye lo ya apostado en esta ronda.':'El importe es el total de tu apuesta en esta ronda.'}}
function seatPosition(index,total){let degrees=90+index*290/total;if(degrees>=235)degrees+=70;const a=degrees*Math.PI/180;return{x:50+35*Math.cos(a),y:51+34*Math.sin(a)}}
function renderSeats(){const s=state;if(!s)return;const players=[...s.players], me=players.findIndex(p=>p.id===s.you);if(me>0)players.push(...players.splice(0,me));const frag=document.createDocumentFragment();positions.clear();players.forEach((p,i)=>{const pos=seatPosition(i,players.length);positions.set(p.id,pos);const el=node('div','seat'+(p.id===s.you?' me':'')+(p.id===s.turn?' turn':'')+(p.folded&&p.in_hand?' folded':''));el.style.left=pos.x+'%';el.style.top=pos.y+'%';el.dataset.pid=p.id;const cards=node('div','seat-cards');p.cards.forEach(c=>cards.append(card(c)));el.append(cards);const body=node('div','seat-main');body.append(node('div','avatar',p.name.slice(0,2).toUpperCase()),node('div','seat-name',p.name+(p.id===s.you?' · TÚ':'')),node('div','seat-money',money(p.stack)));let role=p.id===s.turn?'TU TURNO':p.folded&&p.in_hand?'RETIRADO':p.stack===0&&p.in_hand&&s.active?'ALL-IN':p.connected?'EN LA MESA':'DESCONECTADO';if(p.id===s.turn&&p.id!==s.you)role='JUGANDO';body.append(node('div','seat-role',role));el.append(body,node('div','seat-bet',p.bet?money(p.bet):'—'));if(p.id===s.button)el.append(node('div','button-disc','D'));frag.append(el)});$('seats').replaceChildren(frag)}
function fly(kind,from,to,delay=0){if(reduced)return;const stage=$('stage'), el=node('i',kind==='card'?'fly-card':'fly-chip');el.style.left=from.x+'%';el.style.top=from.y+'%';stage.append(el);const dx=(to.x-from.x)*stage.clientWidth/100,dy=(to.y-from.y)*stage.clientHeight/100;const motion=el.animate([{transform:'translate(-50%,-50%) scale(.75)',opacity:0},{opacity:1,offset:.15},{transform:`translate(calc(-50% + ${dx}px),calc(-50% + ${dy}px)) rotate(${kind==='card'?12:300}deg) scale(1)`,opacity:1}],{duration:kind==='card'?600:760,delay,easing:'cubic-bezier(.2,.6,.25,1)',fill:'both'});motion.onfinish=()=>el.remove();setTimeout(()=>el.remove(),delay+1800)}
function animations(){
 if(!state)return;const key=state.match+':'+state.hand;
 const betChips=pid=>{const from=positions.get(pid);if(from)for(let i=0;i<5;i++)fly('chip',from,{x:48+i,y:66},i*65)};
 if(key!==animScope){animScope=key;if(state.active){$('dealer').classList.add('dealing');setTimeout(()=>$('dealer').classList.remove('dealing'),2000);let i=0;for(let round=0;round<2;round++)for(const p of state.players.filter(p=>p.in_hand))fly('card',{x:50,y:12},positions.get(p.id),i++*90);for(const p of state.players.filter(p=>p.bet>0))betChips(p.id)}}
 if(lastState&&lastState.match===state.match&&lastState.hand===state.hand){
  for(const p of state.players){const old=lastState.players.find(x=>x.id===p.id);const final=state.report.find(r=>r.id===p.id);const total=!state.active&&lastState.active&&final?final.wagered:p.total;if(old&&total>old.total)betChips(p.id)}
  if(state.board.length>lastState.board.length)for(let i=lastState.board.length;i<state.board.length;i++)fly('card',{x:50,y:12},{x:39+i*5.5,y:51},(i-lastState.board.length)*120);
 }
}
function render(){const s=state, me=s.players.find(p=>p.id===s.you);$('network').textContent=online?'En la mesa':'Sin conexión';$('netDot').style.background=online?'#8af0ad':'#dc9270';$('handNo').textContent='Partida '+s.match+' · Mano '+s.hand;$('pot').textContent=money(s.active?s.pot:s.last_pot);$('potCaption').textContent=s.active?'BOTE VIRTUAL':'ÚLTIMO BOTE';$('potChips').hidden=!(s.active?s.pot:s.last_pot);$('phase').textContent=s.ended?'Partida terminada':s.phase;$('board').replaceChildren(...Array.from({length:5},(_,i)=>card(s.board[i])));$('wallet').textContent=money(me.stack);$('myCards').replaceChildren(...(me.cards.length?me.cards:['??','??']).map(card));renderSeats();animations();const key=s.match+':'+s.hand+':'+s.revision;if(s.options&&s.turn===s.you&&turnKey!==key){if(document.activeElement!==$('amount'))$('amount').value=(Math.min(s.options.min,s.options.max)/100).toFixed(2);turnKey=key}controls();renderSide();updateClock()}
function renderSide(){const s=state,lastChat=s.chat.length?s.chat[s.chat.length-1].id:0;if(lastChat!==chatID){chatID=lastChat;const frag=document.createDocumentFragment();for(const m of s.chat){const el=node('div','chat-message'),author=node('div','chat-author',m.name);author.append(node('span','muted',m.time));el.append(author,node('div','',m.text));frag.append(el)}$('chatLog').replaceChildren(frag);$('chatLog').scrollTop=$('chatLog').scrollHeight;if(activePane!=='chat')$('unread').textContent='●'}const profiles=document.createDocumentFragment();for(const p of s.players){const el=node('div','profile-row');el.append(node('strong','',p.name+(p.id===s.you?' · tú':'')),node('div','',money(p.stack)+' · '+(p.connected?'Conectado':'Sin conexión')));const grid=node('div','profile-stats');for(const [label,val,cls] of [['Ganado',p.gained,'plus'],['Perdido',p.lost,'minus'],['Balance neto',p.gained-p.lost,''],['Apostado',p.wagered,'']]){const cell=node('div',cls);cell.append(node('span','',label),node('b','',val<0?signed(val):money(val)));grid.append(cell)}el.append(grid,node('div','muted',p.hands+' manos · '+p.wins+' con botes ganados'));profiles.append(el)}$('profiles').replaceChildren(profiles);const results=document.createDocumentFragment();if(!s.report.length)results.append(node('p','muted',s.ended?s.end_reason:'Todavía no termina una mano.'));for(const r of s.report){const el=node('div','result-row');el.append(node('strong','',r.name),node('div','',r.hand_name||'Sin bote ganado'),node('div','',`Apostó ${money(r.wagered)} · Ganó ${money(r.won)}`),node('div',r.net>=0?'plus':'minus','Neto '+signed(r.net)));if(r.refund)el.append(node('div','muted','Devuelto '+money(r.refund)));if(r.best_five?.length){const cards=node('div','result-cards');r.best_five.forEach(c=>cards.append(card(c)));el.append(cards)}results.append(el)}$('results').replaceChildren(results)}
function updateClock(){if(!state)return;const seconds=Math.max(0,Math.ceil(state.seconds-(performance.now()-stateAt)/1000));const who=state.players.find(p=>p.id===state.turn);$('clock').textContent=state.active?seconds+' s':'—';$('timerBar').style.width=(state.active?seconds/60*100:0)+'%';$('turnText').replaceChildren();if(state.ended)$('turnText').textContent=state.end_reason;else if(state.active){$('turnText').append(node('b','',who?.id===state.you?'ES TU TURNO':'Turno de '+(who?.name||'—')))}else $('turnText').textContent=state.champion!==null?'Partida finalizada. El anfitrión puede reiniciar.':'Esperando que el anfitrión reparta.'}
function receiveEvents(){const current=state.match+':'+state.hand;if(scope!==current){scope=current;announcementQueue=[];dismiss(false)}let fresh=eventCursor===null?(state.active?[]:state.events.slice(-1)):state.events.filter(e=>e.id>eventCursor);eventCursor=state.event_id;fresh=fresh.filter(e=>e.match===state.match&&e.hand===state.hand);if(fresh.some(e=>e.kind==='ended')){announcementQueue=[];dismiss(false);fresh=fresh.filter(e=>e.kind==='ended')}announcementQueue.push(...fresh);if(announcementTimer===null)nextAnnouncement()}
function nextAnnouncement(){if(!announcementQueue.length)return;const event=announcementQueue.shift();const compact=event.kind==='raise'||event.kind==='fold';$('announcement').classList.toggle('compact',compact);$('announcement').setAttribute('aria-modal',String(!compact));$('announcementBadge').textContent=({winner:'LA MANO TIENE GANADOR',fold:'JUGADOR RETIRADO',raise:'LAS APUESTAS SUBEN',ended:'FIN DE LA PARTIDA'})[event.kind];$('announcementTitle').textContent=event.title;$('announcementDetail').textContent=event.detail;$('announcement').hidden=false;if(event.kind==='winner'){for(const r of state.report.filter(r=>r.won>0)){const to=positions.get(r.id);if(to)for(let i=0;i<7;i++)fly('chip',{x:50,y:60},to,i*65)}}announcementTimer=setTimeout(()=>dismiss(),event.kind==='winner'||event.kind==='ended'?7500:1900)}
function dismiss(next=true){clearTimeout(announcementTimer);announcementTimer=null;$('announcement').hidden=true;if(next)nextAnnouncement()}$('dismiss').onclick=()=>dismiss();document.addEventListener('keydown',e=>{if(e.key==='Escape')dismiss()});
try{$('name').value=localStorage.getItem('pokerName')||''}catch{}api('/api/info').then(i=>$('initialBalance').textContent=money(i.starting_stack)).catch(()=>{});poll();setInterval(poll,900);setInterval(updateClock,250);window.addEventListener('resize',()=>{if(state)renderSeats()});document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll()});
</script></body></html>"""


def local_addresses():
    """Sugerencia de IP; no requiere enviar paquetes a Internet."""
    ips = set()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    if sys.platform == "darwin":
        import subprocess
        for interface in ("en0", "en1"):
            try:
                ip = subprocess.run(["/usr/sbin/ipconfig", "getifaddr", interface],
                                    capture_output=True, text=True, timeout=1).stdout.strip()
                if ip:
                    ips.add(ip)
            except (OSError, subprocess.TimeoutExpired):
                pass
    return ", ".join(sorted(ip for ip in ips if not ip.startswith("127."))) or "Consulta la IP en Ajustes de red"


def launch_gui():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        print("Falta Tkinter. En macOS instala Python desde https://www.python.org/downloads/macos/", file=sys.stderr)
        return 1

    class App:
        def __init__(self, root):
            self.root = root
            self.server = self.client = None
            self.webhub = None
            self.token = None
            self.host_key = None
            self.state = None
            self.online = False
            self.rendered_logs = None
            self.received_at = 0
            self.busy = False
            self.result_key = None
            self.event_cursor = None
            self.event_queue = deque()
            self.overlay_job = None
            self.overlay_scope = None
            self.rendered_chat = None
            self.profile_name = tk.StringVar()
            root.title("Póker LAN · Texas Hold'em · v5 · Celulares")
            root.geometry("1160x850")
            root.minsize(1000, 760)
            root.configure(bg="#101b25")
            style = ttk.Style(root)
            style.theme_use("clam")
            style.configure(".", background="#101b25", foreground="#e5edf3", font=("Helvetica", 12))
            style.configure("TLabel", background="#101b25")
            style.configure("TLabelframe", bordercolor="#304353")
            style.configure("TLabelframe.Label", foreground="#9caebb")
            style.configure("TEntry", fieldbackground="#1c2d3b", foreground="#ffffff", insertcolor="#ffffff", padding=5)
            style.map("TEntry", fieldbackground=[("disabled", "#17242e")], foreground=[("disabled", "#91a5b4")])
            style.configure("TButton", background="#2b4052", foreground="#f1f5f9", padding=(10, 8), borderwidth=0)
            style.map("TButton", background=[("disabled", "#1b2a36"), ("active", "#3d5b72")], foreground=[("disabled", "#708595")])
            style.configure("Accent.TButton", background="#20785c", foreground="#ffffff")
            style.map("Accent.TButton", background=[("disabled", "#1b2a36"), ("active", "#299b76")])
            style.configure("Gold.TButton", background="#b9934d", foreground="#101b25")
            style.map("Gold.TButton", background=[("disabled", "#1b2a36"), ("active", "#d5b36e")], foreground=[("disabled", "#708595")])
            style.configure("Treeview", background="#162633", fieldbackground="#162633", foreground="#e5edf3", rowheight=32, borderwidth=0)
            style.configure("Treeview.Heading", background="#243a49", foreground="#f1d99d", font=("Helvetica", 12, "bold"), padding=7)
            style.configure("TNotebook", background="#101b25", borderwidth=0)
            style.configure("TNotebook.Tab", padding=(16, 7), background="#1b2a36")
            style.map("TNotebook.Tab", background=[("selected", "#2b4052")], foreground=[("selected", "#f1d99d")])
            style.configure("Turn.Horizontal.TProgressbar", troughcolor="#1b2a36", background="#d2b36d", borderwidth=0)
            root.protocol("WM_DELETE_WINDOW", self.close)
            frame = ttk.Frame(root, padding=14)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="♠  TEXAS HOLD’EM", foreground="#e7ce94", font=("Helvetica", 24, "bold")).pack(anchor="w")
            ttk.Label(frame, text="DINERO VIRTUAL  ·  Ciegas S/ 0.10 / 0.20  ·  2–10 jugadores  ·  Sin límite").pack(anchor="w", pady=(0, 10))
            connect = ttk.LabelFrame(frame, text="Conexión", padding=8)
            self.connect_frame = connect
            connect.pack(fill="x")
            self.name, self.address = tk.StringVar(value="Jugador"), tk.StringVar(value="127.0.0.1")
            self.port, self.pin = tk.StringVar(value="5050"), tk.StringVar()
            self.entries = []
            for col, (label, var, width) in enumerate((("Nombre", self.name, 17), ("IP anfitrión", self.address, 18),
                                                      ("Puerto", self.port, 7), ("Clave de mesa", self.pin, 15))):
                ttk.Label(connect, text=label).grid(row=0, column=col, sticky="w", padx=4)
                entry = ttk.Entry(connect, textvariable=var, width=width, show="•" if var is self.pin else "")
                entry.grid(row=1, column=col, padx=4, sticky="ew")
                self.entries.append(entry)
            self.host_button = ttk.Button(connect, text="Crear mesa", style="Accent.TButton", command=self.host)
            self.host_button.grid(row=1, column=4, padx=6)
            self.join_button = ttk.Button(connect, text="Conectar", command=self.join)
            self.join_button.grid(row=1, column=5, padx=6)
            ttk.Label(connect, text="Saldo inicial por jugador (S/ virtuales):").grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(7, 0))
            self.bankroll = tk.StringVar(value="10000.00")
            self.bankroll_entry = ttk.Entry(connect, textvariable=self.bankroll, width=12)
            self.bankroll_entry.grid(row=2, column=2, sticky="w", padx=4, pady=(7, 0))
            ttk.Label(connect, text="Lo elige quien crea la mesa.", foreground="#96abbb").grid(row=2, column=3, columnspan=3, sticky="w", padx=4, pady=(7, 0))
            self.network_label = ttk.Label(frame, text="Misma red Wi-Fi/Ethernet. El anfitrión comparte su IP, puerto y clave.")
            self.network_label.pack(anchor="w", pady=6)
            self.status = ttk.Label(frame, text="Crea una mesa o conéctate a una existente.", font=("Helvetica", 13, "bold"))
            self.status.pack(anchor="w", pady=6)
            self.timer = ttk.Progressbar(frame, maximum=TURN_SECONDS, style="Turn.Horizontal.TProgressbar")
            self.timer.pack(fill="x", pady=(0, 8))
            self.canvas = tk.Canvas(frame, height=178, bg="#125442", highlightthickness=0)
            self.canvas.pack(fill="x")
            self.canvas.bind("<Configure>", lambda event: self.draw_cards())
            self.banner = ttk.Label(frame, text="Bienvenidos · Crea una mesa o únete con la IP de tu amigo.",
                                    foreground="#e7ce94", font=("Helvetica", 12, "bold"), wraplength=980)
            self.banner.pack(anchor="w", pady=(8, 6))
            self.tabs = ttk.Notebook(frame)
            self.tabs.pack(fill="both", expand=True, pady=(0, 10))
            table_frame = ttk.Frame(self.tabs)
            self.summary_frame = ttk.Frame(self.tabs)
            history_frame = ttk.Frame(self.tabs)
            self.chat_frame = ttk.Frame(self.tabs, padding=8)
            self.profile_frame = ttk.Frame(self.tabs, padding=10)
            self.tabs.add(table_frame, text="Mesa y apuestas")
            self.tabs.add(self.summary_frame, text="Resultado de la mano")
            self.tabs.add(history_frame, text="Historial")
            self.tabs.add(self.chat_frame, text="Chat global")
            self.tabs.add(self.profile_frame, text="Perfiles")
            self.tabs.bind("<<NotebookTabChanged>>", self.tab_changed)
            compose = ttk.Frame(self.chat_frame)
            compose.pack(side="bottom", fill="x", pady=(6, 0))
            self.chat_input = tk.StringVar()
            self.chat_entry = ttk.Entry(compose, textvariable=self.chat_input)
            self.chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
            self.chat_entry.bind("<Return>", lambda e: self.send_chat())
            self.chat_button = ttk.Button(compose, text="Enviar", style="Accent.TButton", command=self.send_chat)
            self.chat_button.pack(side="right")
            self.chat_view = tk.Text(self.chat_frame, bg="#162633", fg="#edf4f8", relief="flat", height=4,
                                     wrap="word", font=("Helvetica", 13), padx=12, pady=8, state="disabled")
            chat_scroll = ttk.Scrollbar(self.chat_frame, command=self.chat_view.yview)
            chat_scroll.pack(side="right", fill="y")
            self.chat_view.configure(yscrollcommand=chat_scroll.set)
            self.chat_view.tag_configure("author", foreground="#e7ce94", font=("Helvetica", 13, "bold"))
            self.chat_view.pack(fill="both", expand=True)
            profile_header = ttk.Frame(self.profile_frame)
            profile_header.pack(fill="x")
            ttk.Label(profile_header, text="PERFIL DEL JUGADOR", font=("Helvetica", 14, "bold"), foreground="#f1d99d").pack(side="left")
            self.profile_picker = ttk.Combobox(profile_header, textvariable=self.profile_name, state="readonly", width=25)
            self.profile_picker.pack(side="right")
            self.profile_picker.bind("<<ComboboxSelected>>", lambda e: self.render_profile())
            self.profile_balance = ttk.Label(self.profile_frame, font=("Helvetica", 15, "bold"), padding=(0, 8))
            self.profile_balance.pack(anchor="w")
            metrics = ttk.Frame(self.profile_frame)
            metrics.pack(fill="x")
            self.profile_metrics = []
            for col, (title, color) in enumerate((("GANADO", "#75dab0"), ("PERDIDO", "#f5a3a3"), ("BALANCE NETO", "#f1d99d"))):
                box = tk.Frame(metrics, bg="#1d3242", padx=12, pady=10)
                box.grid(row=0, column=col, sticky="ew", padx=4)
                metrics.columnconfigure(col, weight=1, uniform="metrics")
                tk.Label(box, text=title, bg="#1d3242", fg="#cedce5", font=("Helvetica", 11, "bold")).pack(anchor="w")
                value = tk.Label(box, text="—", bg="#1d3242", fg=color, font=("Helvetica", 20, "bold"))
                value.pack(anchor="w")
                self.profile_metrics.append(value)
            self.profile_stats = ttk.Label(self.profile_frame, text="Selecciona un jugador.", padding=(0, 8), wraplength=960)
            self.profile_stats.pack(anchor="w")
            self.summary_title = ttk.Label(self.summary_frame, text="El resumen aparecerá al terminar la primera mano.", padding=6)
            self.summary_title.pack(anchor="w")
            self.winning_label = ttk.Label(self.summary_frame, foreground="#8ee0bd", font=("Helvetica", 13, "bold"), wraplength=1040, padding=(6, 2))
            self.winning_label.pack(anchor="w")
            summary_cols = ("name", "wagered", "won", "refund", "net", "stack", "session")
            self.summary_table = ttk.Treeview(self.summary_frame, columns=summary_cols, show="headings", height=5, selectmode="none")
            for col, title, width in zip(summary_cols, ("Jugador", "Apostó", "Ganó en botes", "Devuelto", "Ganancia neta", "Saldo", "Apostado partida"), (150, 90, 120, 90, 120, 90, 140)):
                self.summary_table.heading(col, text=title)
                self.summary_table.column(col, width=width, anchor="center")
            summary_scroll = ttk.Scrollbar(self.summary_frame, command=self.summary_table.yview)
            summary_scroll.pack(side="right", fill="y")
            self.summary_table.configure(yscrollcommand=summary_scroll.set)
            self.summary_table.pack(fill="both", expand=True)
            self.summary_table.tag_configure("winner", background="#254738", foreground="#f1d99d")
            cols = ("name", "position", "stack", "bet", "total", "state", "cards")
            self.table = ttk.Treeview(table_frame, columns=cols, show="headings", height=5, selectmode="browse")
            self.table.bind("<Double-1>", self.open_profile)
            for col, title, width in zip(cols, ("Jugador", "Posición", "Saldo S/", "Apuesta ronda", "Apostó mano", "Estado", "Cartas"),
                                          (160, 70, 145, 140, 140, 150, 120)):
                self.table.heading(col, text=title)
                self.table.column(col, width=width, anchor="center")
            self.table.pack(side="left", fill="both", expand=True)
            scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
            scroll.pack(side="right", fill="y")
            self.table.configure(yscrollcommand=scroll.set)
            self.table.tag_configure("turn", background="#254738", foreground="#f1d99d")
            actions = ttk.Frame(frame)
            actions.pack(fill="x")
            self.fold_button = ttk.Button(actions, text="Retirarse", command=lambda: self.act("fold"))
            self.call_button = ttk.Button(actions, text="Pasar / igualar", style="Accent.TButton", command=lambda: self.act("call"))
            self.amount = tk.StringVar(value="0.40")
            self.amount_entry = ttk.Entry(actions, textvariable=self.amount, width=9)
            self.raise_button = ttk.Button(actions, text="Apostar / subir a", command=lambda: self.act("raise"))
            self.allin_button = ttk.Button(actions, text="All-in", command=lambda: self.act("allin"))
            manage = ttk.Frame(frame)
            manage.pack(fill="x", pady=(8, 0))
            ttk.Label(manage, text="Doble clic en un jugador para ver su perfil.", foreground="#a6bfce").pack(side="left")
            self.start_button = ttk.Button(manage, text="Repartir mano", style="Accent.TButton", command=self.start_hand)
            for widget in (self.fold_button, self.call_button, self.amount_entry, self.raise_button, self.allin_button):
                widget.pack(side="left", padx=(0, 6))
            self.start_button.pack(side="right")
            self.reset_button = ttk.Button(manage, text="Nueva partida", style="Gold.TButton", command=self.reset_match)
            self.reset_button.pack(side="right", padx=6)
            self.hint = ttk.Label(frame, text="Importe en soles virtuales, total de esta ronda. Ejemplo: 0.50 significa S/ 0.50.")
            self.hint.pack(anchor="w", pady=6)
            self.notice = ttk.Label(frame, text="", foreground="#b5cbd8", wraplength=980)
            self.notice.pack(anchor="w")
            self.history = tk.Text(history_frame, height=6, wrap="word", state="disabled", font=("Helvetica", 11),
                                   bg="#162633", fg="#d3e1ea", relief="flat", padx=10, pady=8)
            history_scroll = ttk.Scrollbar(history_frame, command=self.history.yview)
            history_scroll.pack(side="right", fill="y")
            self.history.configure(yscrollcommand=history_scroll.set)
            self.history.pack(fill="both", expand=True)
            # Aviso grande local, alimentado por los mismos eventos públicos del servidor.
            # No bloquea el hilo de red ni utiliza diálogos modales que pausen la mesa.
            self.overlay = tk.Frame(root, bg="#132d29", highlightbackground="#dfbf77", highlightthickness=3)
            self.overlay_badge = tk.Label(self.overlay, text="♠", bg="#132d29", fg="#dfbf77", font=("Helvetica", 42, "bold"))
            self.overlay_badge.pack(pady=(18, 2))
            self.overlay_title = tk.Label(self.overlay, bg="#132d29", fg="#fff0c9", font=("Helvetica", 26, "bold"), wraplength=780)
            self.overlay_title.pack(fill="x", padx=22, pady=6)
            self.overlay_detail = tk.Text(self.overlay, bg="#132d29", fg="#e0e9e4", font=("Helvetica", 16),
                                          relief="flat", height=5, wrap="word", padx=18, pady=8, state="disabled")
            detail_scroll = ttk.Scrollbar(self.overlay, command=self.overlay_detail.yview)
            detail_scroll.pack(side="right", fill="y", padx=(0, 6))
            self.overlay_detail.configure(yscrollcommand=detail_scroll.set)
            self.overlay_detail.pack(fill="both", expand=True, padx=12)
            ttk.Button(self.overlay, text="Continuar en la mesa", style="Gold.TButton", command=self.dismiss_announcement).pack(pady=14)
            # Reservar primero el pie: las tablas se reducen y desplazan, los controles no desaparecen.
            for widget in (self.tabs, actions, manage, self.hint, self.notice):
                widget.pack_forget()
            self.notice.pack(side="bottom", anchor="w")
            self.hint.pack(side="bottom", anchor="w", pady=4)
            manage.pack(side="bottom", fill="x", pady=(6, 0))
            actions.pack(side="bottom", fill="x")
            self.tabs.pack(fill="both", expand=True, pady=(0, 8))
            self.controls()
            root.after(100, self.poll)

        def tab_changed(self, event=None):
            if hasattr(self, "chat_frame") and self.tabs.select() == str(self.chat_frame):
                self.tabs.tab(self.chat_frame, text="Chat global")
            if hasattr(self, "profile_frame"):
                if self.tabs.select() == str(self.profile_frame):
                    self.canvas.pack_forget()
                elif not self.canvas.winfo_manager():
                    self.canvas.pack(fill="x", before=self.banner)

        def send_chat(self):
            if self.online and self.client:
                text = self.chat_input.get().strip()
                if not text or len(text) > 300:
                    self.notice.configure(text="El chat admite mensajes de 1 a 300 caracteres.")
                    return "break"
                self.client.send({"type": "chat", "text": text})
                self.chat_input.set("")
            return "break"

        def open_profile(self, event=None):
            selected = self.table.selection()
            if selected and self.state:
                p = next((p for p in self.state["players"] if str(p["id"]) == selected[0]), None)
                if p:
                    self.profile_name.set(p["name"])
                    self.render_profile()
                    self.tabs.select(self.profile_frame)

        def render_profile(self):
            if not self.state:
                return
            players = self.state["players"]
            self.profile_picker["values"] = [p["name"] for p in players]
            p = next((p for p in players if p["name"] == self.profile_name.get()), None)
            if p is None:
                p = next(p for p in players if p["id"] == self.state["you"])
                self.profile_name.set(p["name"])
            self.profile_balance.configure(text=f"{p['name']} · Saldo {money(p['stack'])} · {'Conectado' if p['connected'] else 'Desconectado'}")
            for label, value in zip(self.profile_metrics, (p["gained"], p["lost"], p["gained"] - p["lost"])):
                label.configure(text=money(value))
            self.profile_stats.configure(text=f"Manos jugadas: {p['hands']}  ·  Manos con botes ganados: {p['wins']}  ·  Total apostado: {money(p['wagered'])}\nGanado y perdido suman los resultados netos de las manos finalizadas. Se reinician con Nueva partida.")

        def config(self):
            name = self.name.get().strip()
            if not name:
                raise ValueError("Introduce tu nombre.")
            port = int(self.port.get())
            if not 1024 <= port <= 65535:
                raise ValueError("Usa un puerto entre 1024 y 65535.")
            return name, port

        def host(self):
            try:
                _, port = self.config()
                self.host_key = secrets.token_urlsafe(32)
                self.server = Server(port, self.pin.get(), self.host_key, parse_money(self.bankroll.get()))
                self.server.start()
                try:
                    self.webhub = WebHub(self.server, port + 1 if port < 65535 else 0)
                    self.webhub.start()
                except OSError:
                    self.webhub = WebHub(self.server, 0)
                    self.webhub.start()
                self.address.set("127.0.0.1")
                addresses = local_addresses()
                self.network_label.configure(text=f"PC: {addresses}:{port}  ·  Celulares: http://{addresses.split(',')[0].strip()}:{self.webhub.port}", wraplength=1060)
                self.join()
            except (OSError, ValueError) as exc:
                messagebox.showerror("No se pudo crear la mesa", str(exc))

        def join(self):
            try:
                name, port = self.config()
                if self.client:
                    self.client.close()
                self.state = None
                self.busy = True
                self.client = Client(self.address.get().strip(), port,
                                     {"type": "hello", "name": name, "pin": self.pin.get(),
                                      "token": self.token, "host_key": self.host_key})
                self.notice.configure(text="Conectando…")
                self.controls()
            except ValueError as exc:
                messagebox.showerror("Datos inválidos", str(exc))

        def start_hand(self):
            if self.client and self.online:
                self.busy = True
                self.client.send({"type": "start"})
                self.controls()

        def reset_match(self):
            if not self.online or self.busy or not self.state or self.state["active"]:
                return
            if messagebox.askyesno("Nueva partida", f"¿Reiniciar los saldos a {money(self.state['starting_stack'])} virtuales por jugador?\n\nSeguirán conectados. Se borrarán los resultados de esta partida."):
                self.busy = True
                self.client.send({"type": "reset", "revision": self.state["revision"]})
                self.controls()

        def receive_announcements(self, state):
            scope = (state["match"], state["hand"])
            if self.overlay_scope != scope:
                self.event_queue.clear()
                self.dismiss_announcement()
                self.overlay_scope = scope
            events = state.get("events", [])
            if self.event_cursor is None:
                # No repetir retiradas antiguas al conectarse por primera vez.
                fresh = events[-1:] if not state["active"] and events and events[-1]["kind"] in ("winner", "ended") else []
            else:
                fresh = [e for e in events if e["id"] > self.event_cursor]
            self.event_cursor = state.get("event_id", 0)
            if any(e["kind"] == "ended" for e in fresh):
                self.event_queue.clear()
                self.dismiss_announcement()
                fresh = [e for e in fresh if e["kind"] == "ended"]
            self.event_queue.extend(e for e in fresh if (e["match"], e["hand"]) == scope)
            if self.overlay_job is None:
                self.show_announcement()

        def show_announcement(self):
            if not self.event_queue:
                return
            event = self.event_queue.popleft()
            winner = event["kind"] == "winner"
            self.overlay_badge.configure(text={"winner": "♠  GANADOR  ♠", "fold": "SE RETIRA", "raise": "SUBIDA / APUESTA", "ended": "MESA DETENIDA"}[event["kind"]], font=("Helvetica", 24, "bold"))
            self.overlay_title.configure(text=event["title"])
            self.overlay_detail.configure(state="normal")
            self.overlay_detail.delete("1.0", "end")
            self.overlay_detail.insert("1.0", event["detail"])
            self.overlay_detail.configure(state="disabled")
            self.overlay.place(relx=0.5, rely=0.45, anchor="center", relwidth=0.9, height=390)
            self.overlay.lift()
            self.overlay_job = self.root.after(9000 if winner or event["kind"] == "ended" else 2000, self.dismiss_announcement)

        def dismiss_announcement(self):
            if self.overlay_job is not None:
                self.root.after_cancel(self.overlay_job)
                self.overlay_job = None
            self.overlay.place_forget()
            self.show_announcement()

        def act(self, kind):
            if not self.state or not self.online or self.busy:
                return
            packet = {"type": "action", "action": kind, "revision": self.state["revision"]}
            if kind == "raise":
                try:
                    packet["amount"] = parse_money(self.amount.get())
                except ValueError:
                    self.notice.configure(text="Introduce soles con hasta dos decimales: 0.10, 0.50, 2.00…")
                    return
            self.busy = True
            self.client.send(packet)
            self.controls()

        def controls(self):
            state = self.state or {}
            opts = state.get("options", {}) if self.online and not self.busy else {}
            for widget in (self.fold_button, self.call_button):
                widget.configure(state="normal" if opts else "disabled")
            self.raise_button.configure(state="normal" if opts.get("raise") and opts["max"] >= opts["min"] else "disabled")
            self.allin_button.configure(state="normal" if opts.get("allin") else "disabled")
            self.amount_entry.configure(state="normal" if opts.get("raise") else "disabled")
            host_ready = self.online and not self.busy and state.get("host") and not state.get("active")
            eligible = sum(p["connected"] and p["stack"] > 0 for p in state.get("players", []))
            self.start_button.configure(state="normal" if host_ready and eligible >= 2 and not state.get("ended") else "disabled")
            connected = sum(p["connected"] for p in state.get("players", []))
            self.reset_button.configure(state="normal" if host_ready and connected >= 2 and (state.get("hand", 0) > 0 or state.get("ended")) else "disabled")
            self.chat_entry.configure(state="normal" if self.online else "disabled")
            self.chat_button.configure(state="normal" if self.online else "disabled")
            self.host_button.configure(state="disabled" if self.server or self.online or self.busy or self.token else "normal")
            self.join_button.configure(text="Reconectar" if self.token else "Conectar",
                                       state="disabled" if self.online or self.busy else "normal")
            for entry in self.entries:
                entry.configure(state="disabled" if self.online or self.busy or self.token else "normal")
            self.bankroll_entry.configure(state="disabled" if self.online or self.busy or self.server or self.token else "normal")
            if opts:
                self.call_button.configure(text="Pasar" if opts["call"] == 0 else f"Igualar {money(opts['call'])}")
                self.hint.configure(text=f"Por igualar: {money(opts['call'])} · Subida mínima TOTAL: {money(opts['min'])} · Tu máximo: {money(opts['max'])}")
            else:
                self.call_button.configure(text="Pasar / igualar")
                self.hint.configure(text="Importe en soles virtuales, total de esta ronda. Ejemplo: 0.50 significa S/ 0.50.")

        def draw_cards(self):
            self.canvas.delete("all")
            width = max(self.canvas.winfo_width(), 800)
            state = self.state or {}
            self.canvas.create_rectangle(4, 4, width - 4, 174, outline="#bfa766", width=2)
            self.canvas.create_oval(380, 36, width - 224, 137, fill="#103f33", outline="#51836a", width=2)
            board = state.get("board", [])
            own = next((p["cards"] for p in state.get("players", []) if p["id"] == state.get("you")), [])
            self.canvas.create_text(22, 18, text="CARTAS COMUNITARIAS", anchor="w", fill="white", font=("Helvetica", 11, "bold"))
            self.canvas.create_text(width - 202, 18, text="TUS CARTAS", anchor="w", fill="white", font=("Helvetica", 11, "bold"))
            def card(x, code):
                self.canvas.create_rectangle(x + 4, 42, x + 64, 132, fill="#0b352a", outline="")
                self.canvas.create_rectangle(x, 38, x + 60, 128, fill="#f5f2e9" if code else "#246854", outline="#8bb3a6", width=2)
                if code:
                    text = code[0].replace("T", "10") + "\n" + SYMBOLS[code[1]]
                    self.canvas.create_text(x + 30, 83, text=text, fill="#bd3838" if code[1] in "dh" else "#152f29", font=("Helvetica", 23, "bold"))
            for index in range(5):
                card(22 + 72 * index, board[index] if index < len(board) else None)
            for index in range(2):
                card(width - 202 + 74 * index, own[index] if index < len(own) else None)
            pot = state.get("pot", 0) if state.get("active") else state.get("last_pot", 0)
            label = "BOTE EN JUEGO" if state.get("active") else "BOTE DE LA ÚLTIMA MANO"
            self.canvas.create_text((380 + width - 224) / 2, 66, text="BOTE VIRTUAL", fill="#b7c9ba", font=("Helvetica", 10, "bold"))
            self.canvas.create_text((380 + width - 224) / 2, 99, text=money(pot), fill="#f1d99d", font=("Helvetica", 19, "bold"))
            self.canvas.create_text(22, 155, text=f"{label}: {money(pot)}    ·    Apuesta actual: {money(state.get('current', 0) if state.get('active') else 0)}", anchor="w", fill="#f1d99d", font=("Helvetica", 13, "bold"))

        def render(self):
            s = self.state
            self.render_profile()
            chat = s.get("chat", [])
            if chat != self.rendered_chat:
                if chat and self.tabs.select() != str(self.chat_frame):
                    self.tabs.tab(self.chat_frame, text="Chat global ●")
                self.chat_view.configure(state="normal")
                self.chat_view.delete("1.0", "end")
                for msg in chat:
                    self.chat_view.insert("end", f"{msg['time']}  {msg['name']}\n", "author")
                    self.chat_view.insert("end", msg["text"] + "\n\n")
                self.chat_view.configure(state="disabled")
                self.chat_view.see("end")
                self.rendered_chat = chat
            report = {row["id"]: row for row in s.get("report", [])}
            champion = next((p for p in s["players"] if p["id"] == s.get("champion")), None)
            if s.get("ended"):
                self.banner.configure(text=s["end_reason"])
            elif champion:
                self.banner.configure(text=f"GANADOR DE LA PARTIDA: {champion['name']} · {money(champion['stack'])} · Apostó en la partida: {money(champion['wagered'])}")
            elif not s["active"] and report:
                winners = [r for r in report.values() if r["won"] > 0]
                self.banner.configure(text="GANADORES DE BOTES · " + "  /  ".join(f"{r['name']}: ganó {money(r['won'])}, apostó {money(r['wagered'])} ({money(r['net'], True)} netos)" for r in winners))
            elif s["active"]:
                self.banner.configure(text="ES TU TURNO · Elige pasar, igualar, subir o retirarte." if s["turn"] == s["you"] else "Mano en curso · Las apuestas y los saldos se actualizan en directo.")
            else:
                self.banner.configure(text=f"Partida {s.get('match', 1)} · Todos listos. El anfitrión puede repartir al reunir dos jugadores.")
            self.summary_table.delete(*self.summary_table.get_children())
            for row in report.values():
                self.summary_table.insert("", "end", values=(row["name"], money(row["wagered"]), money(row["won"]), money(row["refund"]),
                    money(row["net"], True), money(row["stack"]), money(row["session_wagered"])), tags=("winner",) if row["won"] else ())
            self.summary_title.configure(text=f"Mano {s.get('report_hand', 0)} · Apostó incluye ciegas. Neto = botes ganados + devuelto − apostado."
                                         if report else "El resumen aparecerá al terminar la primera mano.")
            winning_rows = [r for r in report.values() if r["won"]]
            self.winning_label.configure(text="  |  ".join(f"{r['name']}: {r.get('hand_name', '')}  {cards_text(r.get('best_five', []))}" for r in winning_rows))
            result_key = (s["match"], s["hand"], "ended" if s.get("ended") else "active" if s["active"] else "result" if report else "lobby")
            if result_key != self.result_key:
                self.tabs.select(self.summary_frame if report and not s["active"] and not s.get("ended") else 0)
                self.result_key = result_key
            self.table.delete(*self.table.get_children())
            for p in s["players"]:
                position = []
                for key, label in (("button", "D"), ("small", "SB"), ("big", "BB")):
                    if s[key] == p["id"]:
                        position.append(label)
                status = "En espera"
                if p["in_hand"]:
                    status = "Retirado" if p["folded"] else "En mano" if s["active"] else "Fin de mano"
                    if s["active"] and not p["folded"] and p["stack"] == 0:
                        status = "All-in"
                if not p["connected"]:
                    status += " · sin red"
                if s["turn"] == p["id"]:
                    status = "▶ TURNO" + (" · sin red" if not p["connected"] else "")
                cards = " ".join("▧" if c == "??" else c[0].replace("T", "10") + SYMBOLS[c[1]] for c in p["cards"])
                self.table.insert("", "end", iid=str(p["id"]), values=(p["name"] + (" (tú)" if p["id"] == s["you"] else ""),
                                  "/".join(position), money(p["stack"]), money(p["bet"]), money(p["total"] if s["active"] else report.get(p["id"], {}).get("wagered", 0)), status, cards),
                                  tags=("turn",) if s["turn"] == p["id"] else ())
            if s["logs"] != self.rendered_logs:
                self.history.configure(state="normal")
                self.history.delete("1.0", "end")
                self.history.insert("end", "\n".join(s["logs"]))
                self.history.see("end")
                self.history.configure(state="disabled")
                self.rendered_logs = s["logs"]
            self.draw_cards()
            self.controls()

        def poll(self):
            if self.client:
                for _ in range(100):
                    try:
                        msg = self.client.events.get_nowait()
                    except queue.Empty:
                        break
                    kind = msg.get("type")
                    if kind == "welcome":
                        self.token = msg["token"]
                        self.online, self.busy = True, False
                        self.notice.configure(text="Conectado. El anfitrión reparte cuando estén todos listos.")
                    elif kind == "state":
                        previous = self.state
                        self.state = msg
                        self.bankroll.set(f"{Decimal(msg['starting_stack']) / 100:.2f}")
                        self.received_at = time.monotonic()
                        self.online, self.busy = True, False
                        self.connect_frame.pack_forget()
                        if not self.server:
                            self.network_label.configure(text=f"Conectado a {self.address.get()}:{self.port.get()} · Saldo inicial: {money(msg['starting_stack'])} virtuales")
                        if msg["options"] and (not previous or previous["revision"] != msg["revision"]):
                            self.amount.set(f"{Decimal(min(msg['options']['min'], msg['options']['max'])) / 100:.2f}")
                        self.render()
                        self.receive_announcements(msg)
                    elif kind == "error":
                        self.busy = False
                        self.notice.configure(text=msg["message"])
                        if not self.online:
                            self.client.close()
                        self.controls()
                    elif kind == "offline":
                        self.online, self.busy = False, False
                        self.connect_frame.pack(fill="x", before=self.network_label)
                        self.notice.configure(text=f"Partida terminada: {msg['message']}")
                        if self.state:
                            self.state.update(active=False, ended=True, turn=None, end_reason="Se perdió la conexión con el anfitrión. La partida terminó.")
                            self.event_queue.clear()
                            self.dismiss_announcement()
                            self.event_queue.append({"kind": "ended", "title": "PARTIDA TERMINADA", "detail": self.state["end_reason"]})
                            self.show_announcement()
                            self.render()
                        self.controls()
            if self.state:
                s = self.state
                remaining = max(0, int(s["seconds"] - (time.monotonic() - self.received_at)))
                current = next((p["name"] for p in s["players"] if p["id"] == s["turn"]), "—")
                text = f"Partida {s.get('match', 1)} · Mano {s['hand']} · {s['phase']}"
                text += f" · Turno: {current} ({remaining} s)" if s["active"] else " · Partida terminada: inicia una nueva mesa o partida" if s.get("champion") is not None or s.get("ended") else " · Reparte otra mano o inicia una nueva partida"
                self.timer["value"] = remaining if s["active"] else 0
                if not self.online:
                    text += " · SIN CONEXIÓN"
                self.status.configure(text=text)
            self.root.after(100, self.poll)

        def close(self):
            if self.server and not messagebox.askokcancel("Cerrar mesa", "Al cerrar el anfitrión termina la sesión para todos. ¿Cerrar?"):
                return
            if self.client:
                self.client.close()
            if self.server:
                if self.webhub:
                    self.webhub.stop()
                self.server.stop()
            self.root.destroy()

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


def run_tests():
    import unittest

    class PokerTests(unittest.TestCase):
        def test_online_host_transfer_and_empty_table_recovery(self):
            server = Server(0, online=True)
            self.assertEqual(server.listener.getsockname()[0], "127.0.0.1")
            server.start()
            clients = []
            def join(name):
                client = Client("127.0.0.1", server.port, {"type": "hello", "name": name, "pin": ""})
                clients.append(client)
                return client
            def state(client, condition):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    try:
                        event = client.events.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if event.get("type") == "state" and condition(event):
                        return event
                self.fail("No llegó el estado online esperado")
            try:
                a = join("A")
                state(a, lambda s: s["host"])
                b, c = join("B"), join("C")
                state(c, lambda s: len(s["players"]) == 3)
                a.send({"type": "start"})
                state(b, lambda s: s["active"])
                a.close()
                transferred = state(b, lambda s: s["host"] and s["ended"])
                self.assertEqual(sum(p["stack"] for p in transferred["players"]), STACK * 3)
                b.send({"type": "reset", "revision": transferred["revision"]})
                state(b, lambda s: not s["ended"] and s["match"] == 2)
                b.send({"type": "start"})
                state(c, lambda s: s["active"])
                b.close()
                state(c, lambda s: s["host"] and s["ended"])
                c.close()
                deadline = time.monotonic() + 4
                while server.host_pid is not None and time.monotonic() < deadline:
                    threading.Event().wait(.02)
                self.assertIsNone(server.host_pid)
                d = join("D")
                recovered = state(d, lambda s: s["host"])
                self.assertEqual(recovered["you"], 0)
                self.assertEqual(len(recovered["players"]), 3)
                e = join("E")
                state(e, lambda s: not s["host"])
                ready = state(d, lambda s: sum(p["connected"] for p in s["players"]) == 2)
                d.send({"type": "reset", "revision": ready["revision"]})
                state(d, lambda s: not s["ended"])
                d.send({"type": "start"})
                state(e, lambda s: s["active"])
            finally:
                for client in clients:
                    client.close()
                server.stop()

        def test_online_https_origin_cookie_and_health(self):
            import http.client
            server = Server(0, online=True)
            server.start()
            hub = WebHub(server, 0, "https://mesa.example")
            hub.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=5)
                conn.request("GET", "/healthz")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                headers = {"Content-Type": "application/json", "X-Poker": "1", "Origin": "https://mesa.example"}
                conn.request("POST", "/api/join", json.dumps({"name": "Online", "pin": ""}), headers)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn("; Secure", response.getheader("Set-Cookie"))
                response.read()
                headers["Origin"] = "https://otra.example"
                conn.request("POST", "/api/join", "{}", headers)
                response = conn.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                conn.close()
            finally:
                hub.stop()
                server.stop()

        def test_mobile_browser_sessions_privacy_actions_chat_and_leave(self):
            import http.client
            server = Server(0, "mesa")
            server.start()
            hub = WebHub(server, 0)
            hub.start()

            def request(path, body=None, cookie="", extra_headers=None):
                conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=8)
                headers = {"Cookie": cookie}
                if body is not None:
                    headers.update({"Content-Type": "application/json", "X-Poker": "1"})
                headers.update(extra_headers or {})
                conn.request("GET" if body is None else "POST", path, json.dumps(body) if body is not None else None, headers)
                res = conn.getresponse()
                raw = res.read().decode()
                data = raw if path == "/" else json.loads(raw)
                result = (res.status, data, (res.getheader("Set-Cookie") or "").split(";")[0])
                conn.close()
                return result

            def wait_state(cookie, condition):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    status, data, _ = request("/api/state", cookie=cookie)
                    if status == 200 and data["state"] and condition(data["state"]):
                        return data["state"]
                    time.sleep(0.04)
                self.fail("El navegador no recibió el estado esperado.")

            try:
                self.assertIn("CÍRCULO", request("/")[1])
                self.assertEqual(request("/api/state")[0], 401)
                self.assertEqual(request("/api/join", {"name": "A", "pin": "mal"})[0], 400)
                code, data, a = request("/api/join", {"name": "Mac", "pin": "mesa"})
                self.assertEqual(code, 200)
                self.assertTrue(data["state"]["host"])
                code, data, b = request("/api/join", {"name": "Celular", "pin": "mesa"})
                self.assertEqual(code, 200)
                self.assertFalse(data["state"]["host"])
                self.assertNotEqual(a, b)
                request("/api/join", {"name": "Otro", "pin": "mesa"}, a)
                self.assertEqual(len(server.game.players), 2)
                request("/api/command", {"type": "start"}, a)
                sa = wait_state(a, lambda s: s["active"])
                sb = wait_state(b, lambda s: s["active"])
                self.assertEqual(sa["players"][1]["cards"], ["??", "??"])
                self.assertEqual(sb["players"][0]["cards"], ["??", "??"])
                self.assertNotIn("token", json.dumps(sa))
                request("/api/command", {"type": "action", "action": "raise", "amount": 100, "revision": sa["revision"]}, a)
                wait_state(b, lambda s: s["turn"] == 1 and s["current"] == 100)
                request("/api/command", {"type": "chat", "text": "Hola desde mi celular"}, b)
                wait_state(a, lambda s: bool(s["chat"]) and s["chat"][-1]["name"] == "Celular")
                self.assertEqual(request("/api/command", {"type": "start"}, a, {"Origin": "http://otro-sitio.invalid"})[0], 403)
                request("/api/leave", {}, b)
                ended = wait_state(a, lambda s: s["ended"])
                self.assertEqual([p["stack"] for p in ended["players"]], [STACK, STACK])
                code, data, _ = request("/api/join", {"name": "Celular", "pin": "mesa"}, b)
                self.assertEqual(code, 200)
                self.assertEqual(data["state"]["you"], 1)
                self.assertTrue(data["state"]["ended"])
            finally:
                hub.stop()
                server.stop()

        def test_profiles_winning_hand_and_raise_announcement(self):
            g = self.make(2)
            g.start()
            g.action(g.turn, "raise", 100)
            self.assertEqual(g.events[-1]["kind"], "raise")
            self.assertIn("S/ 1.00", g.events[-1]["title"])
            g.action(g.turn, "fold")
            self.assertEqual(g.players[0].gained, 20)
            self.assertEqual(g.players[1].lost, 20)
            self.assertEqual(g.players[0].hands, 1)
            self.assertEqual(g.players[0].wins, 1)
            self.assertEqual(g.report[0]["best_five"], [])
            g.reset()
            self.assertEqual(g.players[0].gained, 0)
            g.start()
            g.board = "As Ks Qs Js Ts".split()
            g.players[0].cards = "2c 3c".split()
            g.players[1].cards = "4d 5d".split()
            g.finish(True)
            for r in g.report:
                self.assertEqual(r["hand_name"], "escalera de color")
                self.assertEqual(rank5(r["best_five"]), (8, 14))
            self.assertIn("ESCALERA DE COLOR", g.events[-1]["detail"])

        def test_disconnect_refunds_and_blocks_restart(self):
            g = self.make()
            g.start()
            g.action(g.turn, "raise", 100)
            g.end_disconnected(1)
            self.assertFalse(g.active)
            self.assertTrue(g.ended)
            self.assertEqual([p.stack for p in g.players], [STACK] * 3)
            self.assertTrue(all(p.total == p.bet == p.gained == p.lost == 0 for p in g.players))
            with self.assertRaises(ValueError):
                g.start()
            with self.assertRaises(ValueError):
                g.action(0, "call")
            self.assertIsNone(g.champion())
            g.reset()
            g.start()
            self.assertTrue(g.active)

        def test_chat_validation_and_bounded_history(self):
            g = self.make(2)
            for text in ("", "   ", "a" * 301, 123):
                with self.assertRaises(ValueError):
                    g.chat_message(0, text)
            revision = g.revision
            for i in range(110):
                g.chat_message(i % 2, f"Hola {i}")
            self.assertEqual(len(g.chat), 100)
            self.assertEqual(g.chat[-1]["name"], "J1")
            self.assertEqual(g.revision, revision)
            g.chat_message(0, "Hola\nmesa")
            self.assertEqual(g.chat[-1]["text"], "Hola mesa")

        def test_money_exact_and_configurable_balance(self):
            self.assertEqual(parse_money("0.10"), 10)
            self.assertEqual(parse_money("2,50"), 250)
            self.assertEqual(money(101), "S/ 1.01")
            self.assertEqual(money(-10, True), "−S/ 0.10")
            for invalid in ("0.001", "-1", "NaN", "Infinity", "hola", "1000000001"):
                with self.assertRaises(ValueError):
                    parse_money(invalid)
            g = Game(parse_money("150.50"))
            g.add("A")
            g.add("B")
            g.start()
            g.action(g.turn, "fold")
            self.assertEqual(sum(p.stack for p in g.players), 30100)
            g.reset()
            self.assertEqual([p.stack for p in g.players], [15050, 15050])

        def test_public_announcements_and_ids(self):
            g = self.make(2)
            g.start()
            g.action(g.turn, "fold")
            a, b = g.snapshot(0), g.snapshot(1)
            self.assertEqual(a["events"], b["events"])
            self.assertEqual([e["kind"] for e in a["events"]], ["fold", "winner"])
            self.assertIn("S/ 0.10", a["events"][0]["detail"])
            self.assertNotIn("cards", str(a["events"]))
            previous_id = a["event_id"]
            g.reset()
            self.assertEqual(g.snapshot(0)["events"], [])
            g.start()
            g.action(g.turn, "fold")
            self.assertGreater(g.events[0]["id"], previous_id)

        def make(self, n=3):
            g = Game()
            for i in range(n):
                g.add(f"J{i}")
            return g

        def test_hand_categories_and_kickers(self):
            hands = ["As Kd Qh 9c 7d", "As Ad Qh 9c 7d", "As Ad Qh Qc 7d", "As Ad Ah 9c 7d",
                     "As 2d 3h 4c 5d", "As Js 8s 6s 2s", "As Ad Ah 9c 9d", "As Ad Ah Ac 7d", "9s Ts Js Qs Ks"]
            for category, hand in enumerate(hands):
                self.assertEqual(rank5(hand.split())[0], category)
            self.assertLess(rank5(hands[4].split()), rank5("2c 3c 4d 5h 6s".split()))
            self.assertGreater(best_hand("As Ad Kc Qc Jd 2h 3h".split()), best_hand("Ah Ac Kd Qd Td 2s 3s".split()))
            self.assertEqual(best_hand("As Ks Qs Js Ts 2h 3d".split()), (8, 14))
            self.assertEqual(best_hand("As Ad Ah Ks Kd Kh 2c".split()), (6, 14, 13))
            self.assertEqual(best_hand("As 2s 3s 4s 6s Kh Kd".split()), (5, 14, 6, 4, 3, 2))
            self.assertEqual(rank5("Qs Kd Ah 2c 3d".split())[0], 0)

        def test_heads_up_and_big_blind_option(self):
            g = self.make(2)
            g.start()
            self.assertEqual((g.button, g.small, g.big, g.turn), (0, 0, 1, 0))
            g.action(0, "call")
            self.assertEqual(g.turn, 1)
            self.assertEqual(g.phase, "Preflop")
            g.action(1, "call")
            self.assertEqual((g.phase, g.turn), ("Flop", 1))
            while g.active:
                g.action(g.turn, "call")
            g.start()
            self.assertEqual((g.button, g.small, g.big, g.turn), (1, 1, 0, 1))

        def test_invalid_actions_and_privacy(self):
            g = self.make()
            g.start()
            with self.assertRaises(ValueError):
                g.action(1, "call")
            with self.assertRaises(ValueError):
                g.action(0, "raise", 30)
            with self.assertRaises(ValueError):
                g.action(0, "raise", STACK + 1)
            s = g.snapshot(0)
            self.assertNotIn("token", str(s))
            self.assertEqual(s["players"][1]["cards"], ["??", "??"])
            self.assertEqual(s["players"][0]["cards"], g.player(0).cards)

        def test_short_allin_does_not_reopen(self):
            g = self.make()
            g.player(1).stack = 150
            g.start()
            g.action(0, "raise", 100)
            g.action(1, "allin")
            g.action(2, "call")
            self.assertEqual(g.turn, 0)
            self.assertFalse(g.options(0)["raise"])
            with self.assertRaises(ValueError):
                g.action(0, "raise", 300)
            g.action(0, "call")
            self.assertEqual(g.phase, "Flop")

        def test_cumulative_short_raises_reopen(self):
            g = self.make(4)
            g.player(0).stack = 150
            g.player(1).stack = 200
            g.start()
            self.assertEqual(g.turn, 3)
            g.action(3, "raise", 100)
            g.action(0, "allin")
            g.action(1, "allin")
            g.action(2, "call")
            self.assertTrue(g.options(3)["raise"])
            self.assertEqual(g.options(3)["min"], 280)

        def test_short_opening_bet(self):
            g = self.make()
            g.player(1).stack = 25
            g.start()
            for _ in range(3):
                g.action(g.turn, "call")
            self.assertEqual(g.turn, 1)
            g.action(1, "allin")
            self.assertEqual(g.current, 5)
            self.assertEqual(g.options(2)["min"], 25)
            with self.assertRaises(ValueError):
                g.action(2, "raise", 20)
            g.action(2, "raise", 25)
            self.assertEqual(g.min_raise, 20)

        def test_short_big_blind_and_late_join(self):
            g = self.make()
            g.player(2).stack = 7
            g.start()
            self.assertEqual(g.options(0)["call"], 20)
            self.assertEqual(g.options(0)["min"], 40)
            newcomer = g.add("Nuevo")
            self.assertNotIn(newcomer.pid, g.hand)
            self.assertEqual(newcomer.cards, [])
            while g.active:
                g.action(g.turn, "call")
            self.assertEqual(sum(p.stack for p in g.players), STACK * 3 + 7)
            g.start()
            self.assertIn(newcomer.pid, g.hand)

        def test_checks_short_allin_and_fold_win_privacy(self):
            g = self.make()
            g.player(2).stack = 25
            g.start()
            for _ in range(3):
                g.action(g.turn, "call")
            g.action(1, "call")  # Check a cero.
            g.action(2, "allin")  # Apuesta incompleta de cinco.
            g.action(0, "call")
            self.assertFalse(g.options(1)["raise"])
            g = self.make(2)
            g.start()
            g.action(g.turn, "fold")
            self.assertFalse(g.showdown)
            self.assertEqual(g.snapshot(0)["players"][1]["cards"], ["??", "??"])

        def test_side_pots_and_uncalled_refund(self):
            g = self.make()
            g.hand, g.button, g.active = [0, 1, 2], 0, True
            g.board = "2c 3d 7h 9s Jc".split()
            for p, cards, amount in zip(g.players, ("As Ad", "Ks Kd", "Qs Qd"), (50, 100, 200)):
                p.cards, p.total, p.stack = cards.split(), amount, 0
            g.finish(True)
            self.assertEqual([p.stack for p in g.players], [150, 100, 100])
            self.assertEqual([r["wagered"] for r in g.report], [50, 100, 200])
            self.assertEqual([r["won"] for r in g.report], [150, 100, 0])
            self.assertEqual([r["refund"] for r in g.report], [0, 0, 100])
            self.assertEqual(sum(r["net"] for r in g.report), 0)

        def test_reset_preserves_connections_and_identity(self):
            g = self.make(2)
            tokens = [p.token for p in g.players]
            g.start()
            with self.assertRaises(ValueError):
                g.reset()
            g.action(g.turn, "fold")
            self.assertEqual(g.last_pot, 30)
            self.assertEqual([r["wagered"] for r in g.report], [10, 20])
            self.assertEqual([r["net"] for r in g.report], [-10, 10])
            self.assertEqual(g.report[1]["refund"], 10)
            g.players[1].connected = False
            revision = g.revision
            g.reset()
            self.assertEqual([p.stack for p in g.players], [STACK, STACK])
            self.assertEqual([p.token for p in g.players], tokens)
            self.assertFalse(g.players[1].connected)
            self.assertEqual(g.report, [])
            self.assertEqual(g.board, [])
            self.assertEqual(g.number, 0)
            self.assertEqual(g.match, 2)
            self.assertGreater(g.revision, revision)
            self.assertTrue(all(p.wagered == 0 for p in g.players))
            g.players[1].connected = True
            g.start()
            self.assertEqual((g.number, g.button), (1, 0))

        def test_champion_and_report_persistence(self):
            g = self.make(2)
            g.start()
            g.board = "2c 3d 7h 9s Jc".split()
            for p, cards in zip(g.players, ("As Ad", "Ks Kd")):
                p.cards, p.total, p.stack = cards.split(), STACK, 0
            g.finish(True)
            self.assertEqual(g.champion(), 0)
            s = g.snapshot(1)
            self.assertEqual(s["champion"], 0)
            self.assertEqual(s["report"][0]["net"], STACK)
            self.assertEqual(s["last_pot"], STACK * 2)
            g.reset()
            self.assertIsNone(g.champion())
            g.start()
            g.action(g.turn, "fold")
            saved = g.report[:]
            g.start()
            self.assertEqual(g.report, saved)
            self.assertEqual(g.report_hand, 1)

        def test_tie_odd_chip_and_folded_money(self):
            g = self.make()
            g.hand, g.button, g.active = [0, 1, 2], 0, True
            g.board = "As Ks Qs Js Ts".split()
            for p, cards in zip(g.players, ("2c 3c", "4c 5c", "6c 7c")):
                p.cards, p.total, p.stack = cards.split(), 5, 0
            g.player(2).folded = True
            g.finish(True)
            self.assertEqual([p.stack for p in g.players], [7, 8, 0])

        def test_allin_runout_and_timeout(self):
            g = self.make(2)
            g.player(0).stack, g.player(1).stack = 5, 7
            g.start()
            self.assertFalse(g.active)
            self.assertEqual(len(g.board), 5)
            self.assertEqual(sum(p.stack for p in g.players), 12)
            g = self.make(2)
            g.start()
            g.deadline = 0
            self.assertTrue(g.tick())
            self.assertFalse(g.active)
            self.assertEqual(sum(p.stack for p in g.players), STACK * 2)

        def test_random_games_chip_conservation(self):
            rng = random.Random(2026)
            for n in range(2, 11):
                g = self.make(n)
                for _ in range(35):
                    if sum(p.stack > 0 for p in g.players) < 2:
                        break
                    g.start()
                    steps = 0
                    while g.active:
                        steps += 1
                        self.assertLess(steps, 500)
                        pid, opts = g.turn, g.options(g.turn)
                        choice = rng.random()
                        if choice < 0.13:
                            g.action(pid, "fold")
                        elif choice < 0.21 and opts["allin"]:
                            g.action(pid, "allin")
                        elif choice < 0.40 and opts["raise"] and opts["max"] >= opts["min"]:
                            g.action(pid, "raise", rng.randint(opts["min"], min(opts["max"], opts["min"] + 60)))
                        else:
                            g.action(pid, "call")
                        self.assertTrue(all(p.stack >= 0 for p in g.players))
                        self.assertEqual(sum(p.stack + p.total for p in g.players), n * STACK)
                    self.assertEqual(sum(p.stack for p in g.players), n * STACK)

        def test_network_join_start_reconnect(self):
            server = Server(0, "test")
            server.start()
            clients = []
            def receive(client, kind, condition=lambda m: True):
                end = time.monotonic() + 4
                while time.monotonic() < end:
                    try:
                        event = client.events.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if event.get("type") == kind and condition(event):
                        return event
                self.fail(f"No llegó mensaje {kind}")
            try:
                a = Client("127.0.0.1", server.port, {"type": "hello", "name": "A", "pin": "test"})
                clients.append(a)
                welcome = receive(a, "welcome")
                b = Client("127.0.0.1", server.port, {"type": "hello", "name": "B", "pin": "test"})
                clients.append(b)
                receive(b, "welcome")
                a.send({"type": "start"})
                state = receive(a, "state", lambda m: m["active"])
                self.assertEqual(state["players"][1]["cards"], ["??", "??"])
                a.send({"type": "action", "action": "call", "revision": -1})
                self.assertIn("cambió", receive(a, "error")["message"])
                b.send({"type": "start"})
                self.assertIn("anfitrión", receive(b, "error")["message"])
                a.send({"type": "action", "action": "call", "revision": state["revision"]})
                receive(b, "state", lambda m: m["turn"] == 1)
                a.close()
                a.thread.join(timeout=2)
                ended = receive(b, "state", lambda m: not m["players"][0]["connected"])
                self.assertTrue(ended["ended"])
                self.assertFalse(ended["active"])
                self.assertEqual([p["stack"] for p in ended["players"]], [STACK, STACK])
                c = Client("127.0.0.1", server.port, {"type": "hello", "token": welcome["token"], "pin": "test"})
                clients.append(c)
                self.assertEqual(receive(c, "welcome")["id"], welcome["id"])
                self.assertTrue(receive(c, "state")["host"])
                b.send({"type": "reset", "revision": server.game.revision})
                self.assertIn("anfitrión", receive(b, "error")["message"])
                c.send({"type": "reset", "revision": server.game.revision})
                receive(c, "state", lambda m: m["match"] == 2)
                c.send({"type": "start"})
                active = receive(c, "state", lambda m: m["active"])
                c.send({"type": "chat", "text": "Hola mesa", "name": "Suplantación"})
                chat_c = receive(c, "state", lambda m: bool(m["chat"]))
                chat_b = receive(b, "state", lambda m: bool(m["chat"]))
                self.assertEqual(chat_c["chat"], chat_b["chat"])
                self.assertEqual(chat_c["chat"][-1]["name"], "A")
                self.assertEqual(chat_c["revision"], active["revision"])
                c.send({"type": "reset", "revision": server.game.revision})
                self.assertIn("termine", receive(c, "error")["message"])
                c.send({"type": "action", "action": "fold", "revision": server.game.revision})
                finished = receive(c, "state", lambda m: not m["active"] and bool(m["report"]))
                b_finished = receive(b, "state", lambda m: not m["active"] and bool(m["report"]))
                self.assertEqual(finished["events"], b_finished["events"])
                c.send({"type": "reset", "revision": finished["revision"]})
                reset = receive(b, "state", lambda m: m["match"] == 3)
                self.assertEqual([p["stack"] for p in reset["players"]], [STACK, STACK])
                self.assertEqual(reset["report"], [])
                self.assertTrue(all(p["connected"] for p in reset["players"]))
            finally:
                for client in clients:
                    client.close()
                for client in clients:
                    client.thread.join(timeout=2)
                server.stop()
            self.assertIsNone(server.failure)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PokerTests)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description="Texas Hold'em LAN: interfaz gráfica o servidor dedicado.")
    parser.add_argument("--test", action="store_true", help="Ejecutar pruebas del motor y de red")
    parser.add_argument("--server", action="store_true", help="Servidor sin ventana; primer cliente controla el reparto")
    parser.add_argument("--web", action="store_true", help="Mesa circular en el navegador y acceso desde celulares")
    parser.add_argument("--desktop", action="store_true", help="Usar la interfaz Tkinter anterior (opcional)")
    parser.add_argument("--web-port", type=int, default=int(os.environ.get("PORT", "5051")), help="Puerto HTTP; también admite PORT")
    parser.add_argument("--online", action="store_true", help="Servidor permanente; transfiere el anfitrión al desconectarse")
    parser.add_argument("--public-url", default=os.environ.get("POKER_PUBLIC_URL", ""), help="Origen HTTPS del alojamiento, sin rutas")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--pin", default=os.environ.get("POKER_PIN", ""), help="Clave compartida de mesa; también admite POKER_PIN")
    args = parser.parse_args()
    if args.test:
        return run_tests()
    if args.desktop and (args.server or args.web or args.online):
        parser.error("Usa --desktop solo, o el modo navegador/servidor.")
    if args.public_url:
        parsed = urlsplit(args.public_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            parser.error("--public-url debe ser un origen HTTPS, por ejemplo https://mi-mesa.onrender.com")
    if not args.desktop:
        if not 1024 <= args.port <= 65535 or not 1024 <= args.web_port <= 65535 or args.port == args.web_port:
            parser.error("Usa dos puertos distintos entre 1024 y 65535.")
        try:
            server = Server(args.port, args.pin, online=args.online)
        except OSError as exc:
            print(f"No se pudo abrir el servidor: {exc}", file=sys.stderr)
            return 1
        server.start()
        try:
            webhub = WebHub(server, args.web_port, args.public_url)
            webhub.start()
        except OSError as exc:
            server.stop()
            print(f"No se pudo abrir el acceso para celulares: {exc}. Prueba --web-port 5052.", file=sys.stderr)
            return 1
        print(f"Servidor activo. IP: {local_addresses()} · Puerto {server.port}. Ctrl+C para cerrar.")
        print("El primer cliente será el anfitrión. Saldo inicial: S/ 10,000.00 virtuales.")
        print(f"En esta Mac: http://127.0.0.1:{webhub.port}")
        for address in local_addresses().split(", "):
            print(f"Celulares / otros equipos: http://{address}:{webhub.port}")
        if args.public_url:
            print(f"Enlace de Internet (requiere alojamiento HTTPS): {args.public_url}")
        if args.online:
            print("Modo permanente: si sale el anfitrión, otro jugador toma el control.")
        if not args.server and not args.online:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{webhub.port}")
        try:
            while server.thread.is_alive():
                server.thread.join(timeout=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            webhub.stop()
            server.stop()
        return 1 if server.failure else 0
    return launch_gui()


if __name__ == "__main__":
    sys.exit(main())
