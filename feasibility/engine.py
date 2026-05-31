"""Candidate implementation goes here.

Implement ``evaluate_offer`` so that it satisfies the rules in ASSIGNMENT.md and
the example expectations in tests/test_cases.py. The dataclasses below define the
required OUTPUT shape (see ASSIGNMENT.md "Output"). You may add helpers, modules,
or rewrite internals freely, but keep ``evaluate_offer``'s signature and the
serialized shape of ``Result`` (so the runner and tests work).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from collections import defaultdict
import itertools

from feasibility.models import (
    Client,
    CreditorRules,
    Offer,
    default_first_payment_date,
    monthly_payment_dates,
    offer_total_cents,
    program_fee_cents,
    round_half_up
)

@dataclass
class Event:
    date: date
    amount_cents: int
    type: str  # "credit" or "debit"
    category: str | None  # "credit" or "debit"

@dataclass
class ScheduleRow:
    date: date
    creditor_payment_cents: int
    program_fee_cents: int
    bank_fee_cents: int
    balance_cents: int


@dataclass
class FundsOption:
    amount_cents: int
    within_guardrail: bool
    reason: str
    # lump-sum only:
    date: date | None = None
    # monthly-increment only:
    num_drafts: int | None = None


@dataclass
class AdditionalFunds:
    lump_sum: FundsOption
    monthly_increment: FundsOption


@dataclass
class Result:
    feasible: bool
    # One of "even", "staircase", or "balloon" — the shape your solution produced
    # (driven by the creditor flags). None when infeasible.
    pay_shape_used: str | None = None
    schedule: list[ScheduleRow] | None = None
    additional_funds: AdditionalFunds | None = None

    def to_dict(self) -> dict:
        out: dict = {"feasible": self.feasible, "pay_shape_used": self.pay_shape_used}
        out["schedule"] = (
            [
                {
                    "date": r.date.isoformat(),
                    "creditor_payment_cents": r.creditor_payment_cents,
                    "program_fee_cents": r.program_fee_cents,
                    "bank_fee_cents": r.bank_fee_cents,
                    "balance_cents": r.balance_cents,
                }
                for r in self.schedule
            ]
            if self.schedule is not None
            else None
        )
        if self.additional_funds is None:
            out["additional_funds"] = None
        else:
            def opt(o: FundsOption) -> dict:
                d = {
                    "amount_cents": o.amount_cents,
                    "within_guardrail": o.within_guardrail,
                    "reason": o.reason,
                }
                if o.date is not None:
                    d["date"] = o.date.isoformat()
                if o.num_drafts is not None:
                    d["num_drafts"] = o.num_drafts
                return d

            out["additional_funds"] = {
                "lump_sum": opt(self.additional_funds.lump_sum),
                "monthly_increment": opt(self.additional_funds.monthly_increment),
            }
        return out

def get_cadence_dates_until_horizon(
    client: Client,
    offer: Offer,
) -> list[date]:

    first_payment_date = (
        offer.first_payment_date
        if offer.first_payment_date is not None
        else default_first_payment_date(client)
    )

    dates = []

    current = first_payment_date

    while current <= client.last_draft_date:
        dates.append(current)
        current = monthly_payment_dates(current, 2)[1]

    return dates

def get_max_k(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
) -> int:

    return min(
        rules.max_terms,
        rules.max_payments,
        len(get_cadence_dates_until_horizon(client, offer))
    )

def validate_payment_constraints(
    payments: list[int],
    rules: CreditorRules,
    offer_total: int,
    shape: str,  # "even" or "balloon"
) -> bool:

    # # print("in the validate func")
    # # print(payments)
    # # print(offer_total)
    # # print(sum(payments))

    if not payments:
        return False


    # --------------------------------------------------
    # Exact sum
    # --------------------------------------------------

    if sum(payments) != offer_total:
        return False

    # --------------------------------------------------
    # Non-decreasing
    # --------------------------------------------------

    for i in range(1, len(payments)):
        if payments[i] < payments[i - 1]:
            return False

    # --------------------------------------------------
    # Floors + Token Rule
    # --------------------------------------------------

    token_count = 0

    for payment_num, payment in enumerate(payments, start=1):

        floor = floor_for_payment(
            payment_num,
            rules,
        )

        if payment < floor:
            return False

        if payment == rules.min_payment_cents:
            token_count += 1

    if token_count > rules.max_token_pays:
        return False

    # --------------------------------------------------
    # Even Pay Validation
    # --------------------------------------------------

    if shape == "even":

        mn = min(payments)
        mx = max(payments)

        # "As equal as possible"
        if mx - mn > 1:
            return False

        # Remainder should be pushed to later payments
        for i in range(1, len(payments)):
            if payments[i] < payments[i - 1]:
                return False

    # --------------------------------------------------
    # Balloon Validation
    # --------------------------------------------------

    elif shape == "balloon":

        if not rules.is_ballooning_allowed:
            return False

    elif shape == "staircase":
        
        # Staircase handles its distinct level segments validation 
        # independently inside validate_staircase_constraints
        pass

    return True

def generate_even_schedule(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    k: int,
    payment_dates: list[date],
):

    total = offer_total_cents(offer)

    base = total // k
    remainder = total % k

    payments = [base] * k

    for i in range(k - remainder, k):
        payments[i] += 1

    if not validate_payment_constraints(
        payments,
        rules,
        total,
        "even"
    ):
        return None, None
    a_f = calculate_additional_funds(client,offer,rules,payments,payment_dates)

    return payments, a_f

def floor_for_payment(
    payment_number: int,
    rules: CreditorRules,
) -> int:

    floor = rules.min_payment_cents

    for from_payment, tier_floor in rules.min_payment_tiers:
        if payment_number >= from_payment:
            floor = max(floor, tier_floor)

    return floor

def minimum_legal_payment(
    payment_number: int,
    tokens_used: int,
    rules: CreditorRules,
) -> tuple[int, int]:

    floor = floor_for_payment(
        payment_number,
        rules,
    )

    # Tier floor is above base minimum.
    # No token involved.
    if floor > rules.min_payment_cents:
        return floor, tokens_used

    # floor == base minimum

    if tokens_used < rules.max_token_pays:
        return rules.min_payment_cents, tokens_used + 1

    return rules.min_payment_cents + 1, tokens_used

def generate_balloon_schedule(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    k: int,
    payment_dates: list[date],
) :

    total = offer_total_cents(offer)

    payments: list[int] = []
    running_sum = 0
    tokens_used = 0

    for payment_num in range(1, k + 1):

        payment, tokens_used = minimum_legal_payment(
            payment_num,
            tokens_used,
            rules,
        )

        remaining_amount = total - running_sum

        if remaining_amount < payment:
            return None

        payments.append(payment)
        running_sum += payment

    surplus = total - running_sum

    payments[-1] += surplus

    if not validate_payment_constraints(
        payments,
        rules,
        total,
        "balloon"
    ):
        return None, None
    a_f = calculate_additional_funds(client,offer,rules,payments,payment_dates)

    return payments, a_f

def validate_staircase_constraints(payments: list[int], rules: CreditorRules, offer_total: int) -> bool:
    """Validates that a staircase configuration honors all hard architectural limits."""
    if not payments:
        return False
    if sum(payments) != offer_total:
        return False

    # Check non-decreasing order
    for i in range(1, len(payments)):
        if payments[i] < payments[i - 1]:
            return False

    # Check floors and tokens
    token_count = 0
    for payment_num, payment in enumerate(payments, start=1):
        floor = floor_for_payment(payment_num, rules)
        if payment < floor:
            return False
        if payment == rules.min_payment_cents:
            token_count += 1

    if token_count > rules.max_token_pays:
        return False

    # Enforce segment count cap
    distinct_levels = len(set(payments))
    if distinct_levels > rules.max_segments:
        return False

    return True

def generate_staircase_schedule(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    k: int,
    payment_dates: list[date],
):
    """Generates an optimal staircase schedule strictly utilizing rules.max_segments,

    and returns the configuration that maximizes front-loaded program fees.
    """
    total = offer_total_cents(offer)
    
    # -------------------------------------------------------------
    # PHASE 1: Generate all structural variations for max_segments
    # -------------------------------------------------------------
    def generate_all_valid_staircases() -> list[list[int]]:
        floors = []
        tokens_used = 0
        for payment_num in range(1, k + 1):
            floor, tokens_used = minimum_legal_payment(payment_num, tokens_used, rules)
            # # print(floor)
            floors.append(floor)

        if sum(floors) > total:
            return []

        num_segments = rules.max_segments
        
        # Boundary fallback if max_segments is explicitly 1
        if num_segments == 1:
            if total % k == 0 and all((total // k) >= f for f in floors):
                candidate = [total // k] * k
                if validate_staircase_constraints(candidate, rules, total):
                    return [candidate]
            return []

        valid_schedules = []
        
        # Slicing the k items into block partitions matching max_segments
        for splits in itertools.combinations(range(1, k), num_segments - 1):
            bounds = [0] + list(splits) + [k]
            seg_sizes = [bounds[i+1] - bounds[i] for i in range(num_segments)]
            
            # Find baseline floor for each segment block
            seg_floors = []
            for i in range(num_segments):
                start, end = bounds[i], bounds[i+1]
                seg_floors.append(max(floors[start:end]))
            
            lifted_sum = sum(f * s for f, s in zip(seg_floors, seg_sizes))
            if lifted_sum > total:
                continue
                
            seg_values = list(seg_floors)
            remainder = total - lifted_sum
            # # print(seg_sizes)
            # # print(seg_values)
            # # print(remainder)
            
            # Distribute excess remainder backward from the last segment block
            for i in range(num_segments - 1, -1, -1):
                if remainder <= 0:
                    break
                    
                if i == num_segments - 1:
                    if remainder % seg_sizes[i] == 0:
                        seg_values[i] += remainder // seg_sizes[i]
                        remainder = 0
                    else:
                        alloc = remainder // seg_sizes[i]
                        seg_values[i] += alloc
                        remainder -= alloc * seg_sizes[i]
                else:
                    max_possible_val = seg_values[i+1]
                    max_add_per_item = max_possible_val - seg_values[i]
                    max_total_absorb = max_add_per_item * seg_sizes[i]
                    
                    amount_to_absorb = min(remainder, max_total_absorb)
                    alloc = amount_to_absorb // seg_sizes[i]
                    
                    seg_values[i] += alloc
                    remainder -= alloc * seg_sizes[i]
            # # print(seg_values)
            # # print(remainder)
            if remainder == 0:
                candidate = []
                for idx, size in enumerate(seg_sizes):
                    candidate.extend([seg_values[idx]] * size)
                    
                if validate_staircase_constraints(candidate, rules, total):
                    # print("candidate")
                    # print(candidate)
                    # print(sum(candidate)," ",total)
                    if validate_payment_constraints(candidate, rules, total, "staircase"):
                        # payment_dates = get_cadence_dates_until_horizon(client, offer)[:k]
                        # if simulate_for_feasibility(client, offer, rules, candidate, payment_dates):
                        #     if candidate not in valid_schedules:
                        #         valid_schedules.append(candidate)
                        if candidate not in valid_schedules:
                            valid_schedules.append(candidate)

        return valid_schedules

    # Execute Phase 1 data mining
    candidate_pool_2 = generate_all_valid_staircases()
    if not candidate_pool_2:
        return None, None
    a_f = None
    for item in candidate_pool_2:
        # print(item)
        temp_a_f = calculate_additional_funds(client,offer,rules,item,payment_dates)
        if temp_a_f is not None:
            if a_f is None:
                a_f = temp_a_f
            else:
                if a_f.lump_sum.amount_cents > temp_a_f.lump_sum.amount_cents:
                    a_f.lump_sum = temp_a_f.lump_sum
                if a_f.monthly_increment.amount_cents > temp_a_f.monthly_increment.amount_cents:
                    a_f.monthly_increment = temp_a_f.monthly_increment
    
    payment_dates = get_cadence_dates_until_horizon(client, offer)[:k]
    candidate_pool = []
    for item in candidate_pool_2:
        if simulate_for_feasibility(client, offer, rules, item, payment_dates):
            candidate_pool.append(item)


    # -------------------------------------------------------------
    # PHASE 2: Select the layout with the earliest fee completion
    # -------------------------------------------------------------
    best_schedule = None
    earliest_completion_date = date.max
    
    # Generate mock timeline space mapping for evaluation
    cadence_dates = get_cadence_dates_until_horizon(client, offer)
    payment_dates = cadence_dates[:k]
    fee_only_dates = cadence_dates[k:]

    for candidate in candidate_pool:
        # Pass the variation through your existing timeline fee collector pipeline
        events, remaining_fee = build_schedule_events(
            client=client, # Ensure your outer loop client gets parsed or accessed here
            offer=offer,
            rules=rules,
            payments=candidate,
            payment_dates=payment_dates,
            fee_only_dates=fee_only_dates,
        )
        
        current_completion_date = None
        running_fee = program_fee_cents(offer, rules)
        
        # Scan forward to locate the exact completion threshold date
        for event in events:
            if event.category == "program_fee":
                running_fee -= event.amount_cents
                if running_fee <= 0:
                    current_completion_date = event.date
                    break
        
        if current_completion_date and current_completion_date < earliest_completion_date:
            earliest_completion_date = current_completion_date
            best_schedule = candidate

    # Fallback to structural top seed if timeline variants finish in a dead tie
    # return best_schedule if best_schedule is not None else candidate_pool[0]
    return (best_schedule, a_f) if best_schedule is not None else (None, a_f)

def simulate_for_feasibility(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    payments: list[int],
    payment_dates: list[date],
) -> bool:

    events: list[Event] = []

    # --------------------------------------------------
    # Future ledger entries only
    # --------------------------------------------------

    for entry in client.ledger:

        if entry.date <= client.as_of_date:
            continue

        events.append(
            Event(
                date=entry.date,
                amount_cents=entry.amount_cents,
                type=entry.type,
                category=None
            )
        )

    # --------------------------------------------------
    # Creditor payments + bank fees
    # --------------------------------------------------

    for payment_date, payment_amount in zip(
        payment_dates,
        payments,
    ):

        events.append(
            Event(
                date=payment_date,
                amount_cents=payment_amount,
                type="debit",
                category=None
            )
        )

        events.append(
            Event(
                date=payment_date,
                amount_cents=rules.bank_fee_cents,
                type="debit",
                category=None
            )
        )

    # --------------------------------------------------
    # Credits before debits on same date
    # --------------------------------------------------

    events.sort(
        key=lambda e: (
            e.date,
            0 if e.type == "credit" else 1,
        )
    )

    # --------------------------------------------------
    # Run simulation
    # --------------------------------------------------

    all_possible_cadence_dates = get_cadence_dates_until_horizon(client, offer)
    absolute_last_cadence_date = all_possible_cadence_dates[-1] if all_possible_cadence_dates else None
    program_fee_checked = False
    # print("absolute_last_cadence_date - ",absolute_last_cadence_date)

    total_program_fee = program_fee_cents(
        offer,
        rules,
    )

    balance = client.current_balance_cents

    for event in events:
        # print("event")
        # print(event.type," ", event.amount_cents)

        if absolute_last_cadence_date and not program_fee_checked:
            if event.date >= absolute_last_cadence_date:
                # print("In the if block")
                # print(event.date," ",absolute_last_cadence_date)
                # print(balance," ",total_program_fee)
                # If the balance right now cannot sustain the fee, it's infeasible
                if balance < total_program_fee:
                    return False
                balance -= total_program_fee
                program_fee_checked = True

        if event.type == "credit":
            balance += event.amount_cents
        else:
            balance -= event.amount_cents

        if balance < 0:
            return False
        # print("balance - ",balance)

    # --------------------------------------------------
    # Can we still collect the program fee?
    # --------------------------------------------------

    # print("checking program_fee feasibility")
    # print("balance - ",balance,", total_program_fee - ",total_program_fee)

    if not program_fee_checked:
        # print("checking program_fee feasibility at simulation termination boundary")
        return balance >= total_program_fee
        
    # return balance >= total_program_fee
    return True

def check_feasibility(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
):

    cadence_dates = get_cadence_dates_until_horizon(
        client,
        offer,
    )

    max_k = min(
        rules.max_terms,
        rules.max_payments,
        len(cadence_dates),
    )
    a_f = None
    for k in range(max_k, 0, -1):

        payment_dates = cadence_dates[:k]

        if rules.even_pays:

            payments, temp_a_f = generate_even_schedule(
                client,
                offer,
                rules,
                k,
                payment_dates
            )

        elif rules.is_ballooning_allowed:

            payments, temp_a_f = generate_balloon_schedule(
                client,
                offer,
                rules,
                k,
                payment_dates,
            )

        else:

            payments, temp_a_f = generate_staircase_schedule(
                client,
                offer,
                rules,
                k,
                payment_dates,
            )

        if temp_a_f is not None:
            if a_f is None:
                a_f = temp_a_f
            else:
                if a_f.lump_sum.amount_cents > temp_a_f.lump_sum.amount_cents:
                    a_f.lump_sum = temp_a_f.lump_sum
                if a_f.monthly_increment.amount_cents > temp_a_f.monthly_increment.amount_cents:
                    a_f.monthly_increment = temp_a_f.monthly_increment

        if payments is None:
            continue

        feasible = simulate_for_feasibility(
            client,
            offer,
            rules,
            payments,
            payment_dates,
        )

        if feasible:
            return True, k, payment_dates, payments, a_f

    return False, None, None, None, a_f

def build_schedule_events(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    payments: list[int],
    payment_dates: list[date],
    fee_only_dates: list[date],
):
    from feasibility.models import program_fee_cents

    total_fee = program_fee_cents(offer, rules)
    remaining_fee = total_fee

    events: list[Event] = []

    # -------------------------
    # 1. Ledger (future only)
    # -------------------------
    for e in client.ledger:
        if e.date <= client.as_of_date:
            continue

        events.append(
            Event(
                date=e.date,
                amount_cents=e.amount_cents,
                type=e.type,
                category="ledger",
            )
        )

    # -------------------------
    # 2. Creditor + bank events
    # -------------------------
    for d, p in zip(payment_dates, payments):

        events.append(
            Event(
                date=d,
                amount_cents=p,
                type="debit",
                category="creditor_payment",
            )
        )

        events.append(
            Event(
                date=d,
                amount_cents=rules.bank_fee_cents,
                type="debit",
                category="bank_fee",
            )
        )

    # -------------------------
    # 3. Dummy cadence events
    # -------------------------
    for d in fee_only_dates:
        events.append(
            Event(
                date=d,
                amount_cents=0,
                type="credit",
                category="dummy",
            )
        )

    # -------------------------
    # 4. Sort
    # -------------------------
    priority = {
        "ledger": 0,
        "creditor_payment": 1,
        "bank_fee": 2,
        "dummy": 3,
    }

    events.sort(key=lambda e: (e.date, priority[e.category]))

    # -------------------------
    # 5. Simulation
    # -------------------------
    balance = client.current_balance_cents

    final_events: list[Event] = []
    fee_event_indices: list[int] = []

    for event in events:

        final_events.append(event)

        if event.type == "credit":
            balance += event.amount_cents
        else:
            balance -= event.amount_cents

        # -------------------------
        # Fee collection trigger
        # -------------------------
        if (
            event.category in {"bank_fee", "dummy"}
            and remaining_fee > 0
            and balance > 0
        ):
            fee_amount = min(balance, remaining_fee)

            fee_event = Event(
                date=event.date,
                amount_cents=fee_amount,
                type="debit",
                category="program_fee",
            )

            final_events.append(fee_event)

            fee_event_indices.append(len(final_events) - 1)

            balance -= fee_amount
            remaining_fee -= fee_amount

        # -------------------------
        # Repair step
        # -------------------------
        while balance < 0:

            deficit = -balance

            if not fee_event_indices:
                return False, [], total_fee

            idx = fee_event_indices[-1]
            fee_event = final_events[idx]

            take = min(fee_event.amount_cents, deficit)

            fee_event.amount_cents -= take

            balance += take
            remaining_fee += take

            if fee_event.amount_cents == 0:
                fee_event_indices.pop()

    # return True, final_events, remaining_fee
    return final_events, remaining_fee
  
def build_final_schedule(
    client: Client,
    events: list[Event],
    cadence_dates: set[date],
) -> list[ScheduleRow]:

    schedule_map = {}   # date -> ScheduleRow
    schedule_index = {} # date -> index in list
    schedule_list = []

    running_balance = client.current_balance_cents

    for e in events:

        # -------------------------
        # 1. update global balance
        # -------------------------
        if e.type == "credit":
            running_balance += e.amount_cents
        else:
            running_balance -= e.amount_cents

        # -------------------------
        # 2. only care about cadence dates
        # -------------------------
        if e.date not in cadence_dates:
            continue

        # -------------------------
        # 3. create row if missing
        # -------------------------
        if e.date not in schedule_map:

            row = ScheduleRow(
                date=e.date,
                creditor_payment_cents=0,
                program_fee_cents=0,
                bank_fee_cents=0,
                balance_cents=0,
            )

            schedule_map[e.date] = row
            schedule_index[e.date] = len(schedule_list)
            schedule_list.append(row)

        row = schedule_map[e.date]

        # -------------------------
        # 4. update visible fields
        # -------------------------
        if e.category == "creditor_payment":
            row.creditor_payment_cents += e.amount_cents

        elif e.category == "bank_fee":
            row.bank_fee_cents += e.amount_cents

        elif e.category == "program_fee":
            row.program_fee_cents += e.amount_cents

        # -------------------------
        # 5. always update balance snapshot
        # -------------------------
        row.balance_cents = running_balance

    return schedule_list

def calculate_additional_funds(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
    payments: list[int],
    payment_dates: list[date]
) -> AdditionalFunds:
    """Calculates the exact minimum Lump Sum and Monthly Increment funding profiles

    required to make an otherwise impossible offer feasible based on a targeted
    payment sequence configuration.
    """
    total_offer = offer_total_cents(offer)
    total_fee = program_fee_cents(offer, rules)
    
    # Identify the absolute outer boundaries for chronological tracking
    all_cadence_dates = get_cadence_dates_until_horizon(client, offer)
    last_cadence_date = all_cadence_dates[-1] if all_cadence_dates else None

    # -------------------------------------------------------------------------
    # 1. CHRONOLOGICAL EVENT TIMELINE COMPILATION
    # -------------------------------------------------------------------------
    events = []
    
    # Future ledger entries only (> as_of_date)
    for entry in client.ledger:
        if entry.date > client.as_of_date:
            events.append(
                Event(
                    date=entry.date, 
                    amount_cents=entry.amount_cents, 
                    type=entry.type, 
                    category="ledger"
                )
            )
            
    # Creditor drops + bank fees
    for d, p in zip(payment_dates, payments):
        events.append(
            Event(
                date=d, 
                amount_cents=p, 
                type="debit", 
                category="creditor"
            )
        )
        if rules.bank_fee_cents > 0:
            events.append(
                Event(
                    date=d, 
                    amount_cents=rules.bank_fee_cents, 
                    type="debit", 
                    category="bank_fee"
                )
            )
            
    # Force the total program fee debit entirely onto the final cadence milestone
    if last_cadence_date:
        events.append(
            Event(
                date=last_cadence_date, 
                amount_cents=total_fee, 
                type="debit", 
                category="program_fee"
            )
        )

    # Sort: Credits before debits on matching calendar days
    priority = {"ledger": 0, "creditor": 1, "bank_fee": 2, "program_fee": 3}
    events.sort(key=lambda e: (e.date, 0 if e.type == "credit" else 1, priority.get(e.category, 4)))

    # -------------------------------------------------------------------------
    # 2. RUN DIAGNOSTIC SIMULATION TIMELINE PASS
    # -------------------------------------------------------------------------
    balance = client.current_balance_cents
    
    first_negative_date = None
    max_deficit = 0
    
    max_required_increment = 0
    future_drafts_count = 0
    
    # Track overall future drafts count for final guardrail payload mapping
    total_future_drafts = sum(1 for e in client.ledger if e.date > client.as_of_date and e.type == "credit")

    for event in events:
        # Track our progress across cash inflows
        if event.type == "credit" and event.category == "ledger":
            future_drafts_count += 1
            
        if event.type == "credit":
            balance += event.amount_cents
        else:
            balance -= event.amount_cents

        # Evaluate structural deficits
        if balance < 0:
            deficit = abs(balance)
            
            # Capture worst-case tracking vectors for Lump Sum
            if deficit > max_deficit:
                max_deficit = deficit
                if first_negative_date is None:
                    first_negative_date = event.date
            
            # Calculate Monthly Increment needed at this specific point in time
            if future_drafts_count > 0:
                calc_val = round_half_up(deficit / future_drafts_count)
                if calc_val > max_required_increment:
                    max_required_increment = calc_val

    # -------------------------------------------------------------------------
    # 3. CONSTRUCT SAFETY GUARDRAILS PANELS
    # -------------------------------------------------------------------------
    # Lump Sum Payload Formulation
    lump_amount = max_deficit
    lump_date = first_negative_date if first_negative_date else (last_cadence_date if last_cadence_date else client.last_draft_date)
    lump_limit = round_half_up(0.65 * total_offer)
    lump_within = lump_amount <= lump_limit
    lump_reason = "" if lump_within else f"Lump sum {lump_amount} exceeds maximum allowed safety threshold of {lump_limit} cents."

    # Monthly Increment Payload Formulation
    inc_amount = max_required_increment
    inc_limit = max(10000, round_half_up(0.40 * client.draft_amount_cents))
    inc_within = inc_amount <= inc_limit
    inc_reason = "" if inc_within else f"Monthly increment {inc_amount} exceeds maximum allowed safety threshold of {inc_limit} cents."

    # -------------------------------------------------------------------------
    # 4. PACKAGE DATA OUTPUT OBJECT
    # -------------------------------------------------------------------------
    return AdditionalFunds(
        lump_sum=FundsOption(
            amount_cents=lump_amount,
            date=lump_date,
            within_guardrail=lump_within,
            reason=lump_reason
        ),
        monthly_increment=FundsOption(
            amount_cents=inc_amount,
            num_drafts=total_future_drafts,
            within_guardrail=inc_within,
            reason=inc_reason
        )
    )

# def evaluate_offer(client: Client, offer: Offer, rules: CreditorRules) -> Result:
#     """Evaluate a single offer. See ASSIGNMENT.md for the full specification.

#     Return a Result with feasible=True and a schedule when the offer fits, or
#     feasible=False with additional_funds (minimum lump sum AND minimum monthly
#     increment) when it does not.
#     """
#     # check_for_edge_cases()
#     raise NotImplementedError("Implement evaluate_offer — see ASSIGNMENT.md")

def evaluate_offer(
    client: Client,
    offer: Offer,
    rules: CreditorRules,
) -> Result:

    # -----------------------------
    # 1. Feasibility check
    # -----------------------------
    feasible, k, payment_dates, payments, a_f = check_feasibility(
        client,
        offer,
        rules,
    )

    # -----------------------------
    # 2. If infeasible
    # -----------------------------
    if not feasible:
        return Result(
            feasible=False,
            pay_shape_used=None,
            schedule=None,
            additional_funds=a_f,
        )

    # -----------------------------
    # 3. Decide pay shape
    # -----------------------------
    if rules.even_pays:
        pay_shape = "even"
    elif rules.is_ballooning_allowed:
        pay_shape = "balloon"
    else:
        pay_shape = "staircase"

    # -----------------------------
    # 4. Build fee-only cadence dates
    # -----------------------------
    all_cadence_dates = payment_dates  # already k-based
    fee_only_dates = all_cadence_dates[k:]

    # -----------------------------
    # 5. Build + simulate full event schedule
    # -----------------------------
    events, remaining_fee = build_schedule_events(
        client=client,
        offer=offer,
        rules=rules,
        payments=payments,
        payment_dates=payment_dates,
        fee_only_dates=fee_only_dates,
    )

    # -----------------------------
    # 6. Convert to output schedule
    # -----------------------------
    schedule = build_final_schedule(client,events,all_cadence_dates)

    # -----------------------------
    # 7. Final result
    # -----------------------------
    return Result(
        feasible=True,
        pay_shape_used=pay_shape,
        schedule=schedule,
        additional_funds=None,
    )