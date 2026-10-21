# Support Ticket Classifier

This app reads a customer support ticket and suggests which team should handle it.
Enter a complaint in the browser, select email or web form, and submit it for classification.

The result includes:

- The issue category: order, payment, delivery, product, account, refund, or other.
- The suggested team, priority, and customer sentiment.
- A confidence score and a short explanation.
- A flag when the ticket needs human review.

For example, a complaint about being charged twice can be classified as a payment
issue and assigned to the payments team. The app returns this suggestion; it does
not create or assign a ticket in an external support system.

Before sending the text to the AI provider, the app masks email addresses, phone
numbers, and card-like numbers that match its patterns. It also checks for attempts
to override the classifier's instructions. Failed classifications fall back to
human review.

Redis limits classification requests to 10 per IP address every 60 seconds by
default. The browser shows how many requests remain. The same classification
workflow is available through the API.
