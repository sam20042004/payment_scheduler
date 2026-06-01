```bash
# evaluate a single case (prints the Result as JSON)
python run.py cases/case1_feasible_even

# tests
pytest -q
```

My Approach - 
So the first step was to try calculating the feasibility 
To know the feasibility - I tried making the worst case possible, i.e. the most back-loaded schedule, and tried simulating it, if it ain't feasible then there is no other feasible solution, i can prove it.

for Even - just took my the max k, and distributed the offer_total over all k,
for Balloon - just took the minimum possible value at each date, and just add the remaining offer_total to the last cadence date.
for staircase - always consider max_segment, first make each segment viable(each must have same value and the minimum possible), and then tried using the remaining sum to increase the value of the segments from the back side.

so after getting the payment schedule, for feasibility, we can just assume that we collected our program_fee on the last cadence date (the worst case), and then tried the simulation, if net balance at each date >= 0 then feasible otherwise infeasible, 

Interestingly, the payment schedule I made above was the final answer, i can prove it, just we need to distribute the program fee from the starting cadence date, as high as possible,

Program fee allocation - 
starting from the each cadence date, after calcualting, every debit and credit on that date, I tried giving net balance whole to the program fee, and then next date, at any point, if my balance becomes -ve, then i tried reducing or removing the program fee from the recently processed cadence dates, and keep doing it until balance becomes >= 0, and reassign the reduced/removed program fee to the next dates, because we have already calcualted the feasibility, and we know that this program fee is feasible, so we are just trying to allocate that as early as possible

if infeasible - 
interestingly, the same payment schedule can be used to calculate the lump sum and monthly increment, i can prove it.
Lump Sum
I considered the same schedule where we were collecting program fee at the last cadence date, as our target here is to make the schedule feasible, not to collect the program fee as ealy as possible, and then i ran siumlation
and found the most -ve balance at any date, this becomes our lump sum and first date where balance became -ve becomes our date of applying the lump sum

Monthly increment
Considering the same schedule with program fee collected at the last cadence date, while running simulation, i tried maintaining count of draft dates passed (after the as_of_date), and whenever the balance becomes -ve, i tried calculating a number such that (no_of_draft_dates_passed * number >= abs(balance)), and took the maximum of this and it became our monthly increment, no_of_draft dates will be the future draft dates after the as_of_date.


