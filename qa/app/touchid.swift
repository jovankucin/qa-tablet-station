// The person's OK for one ClickUp write (a Swiss Army Knife ticket), asked on this Mac (2026-11-03).
//
// touchid "<reason>"   shows the system prompt (Touch ID, or the account password) with that reason.
// Exit 0: approved. 1: cancelled or failed. 2: the policy is not available on this Mac. 64: no reason given.
//
// approvals.py compiles this once with swiftc into qa/app/.touchid and runs it with a 90 s timeout; nothing else
// runs it. It prints nothing and reads nothing but its one argument.
import Foundation
import LocalAuthentication

let args = CommandLine.arguments
guard args.count == 2, !args[1].isEmpty else {
    exit(64)
}
let context = LAContext()
var probe: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &probe) else {
    exit(2)
}
// the reply comes on a private queue; the main thread waits for it (written once, read after the signal)
final class Answer: @unchecked Sendable {
    var approved = false
}
let answer = Answer()
let done = DispatchSemaphore(value: 0)
context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: args[1]) { ok, _ in
    answer.approved = ok
    done.signal()
}
done.wait()
exit(answer.approved ? 0 : 1)
